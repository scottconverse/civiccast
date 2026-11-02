# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest

from civiccast.cg.service import build_emergency_overlay, build_overlay_contract
from civiccast.egress.branding import build_branding_filter_plan
from civiccast.egress.caption_embed import SidecarCaptionEmbedder
from civiccast.egress.cg_bridge import (
    CG_EGRESS_PROOF_BOUNDARY,
    build_cg_overlay_egress_proof,
)
from civiccast.egress.daemon import _RESTART_ESCALATION_STREAK, EgressDaemon
from civiccast.egress.encoder_strategy import EncoderStartRequest, EncoderStartResult
from civiccast.egress.errors import SourcePrepareError
from civiccast.egress.gst.strategy import GstPlayoutStrategy
from civiccast.egress.models import (
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
    EgressStateRow,
)
from civiccast.egress.preparer import PreparedSegmentRecord, SourcePreparationReport
from civiccast.egress.source_plan import ScheduleSourcePlanProvider, SlateSourceGenerator
from civiccast.egress.store import InMemoryEgressStore
from civiccast.egress.supervisor import PlayoutSupervisor
from civiccast.schedule.models import ScheduleItemResponse, StaffAssetRow


def _write_fake_reload_status(
    work_dir: Path, channel_id: str, command_id: str | None, result: str
) -> None:
    """F1 redesign test helper: simulates ``worker.py``'s
    ``_write_reload_status`` -- writes the file ``EgressDaemon.
    _poll_reload_settlement`` polls for. ``command_id`` falls back to a fresh
    uuid (mirroring ``worker.py``'s own fallback) so a caller that doesn't
    pass one still produces a matchable id."""
    channel_dir = work_dir / channel_id
    channel_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"id": command_id or uuid.uuid4().hex, "result": result})
    (channel_dir / "reload-status.json").write_text(payload, encoding="utf-8")


class _FakeProcess:
    def __init__(self, *, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0


class _StubbornFakeProcess:
    """Item 85 hostile-review follow-up: a worker so wedged that even
    ``terminate()`` (SIGTERM / a Windows console-control-style request) is
    never observed -- ``poll()`` keeps reporting "still alive" no matter how
    many times ``terminate()`` is called. Only ``kill()`` (SIGKILL / a forced
    Windows ``TerminateProcess``) actually ends it, with a distinct non-zero
    returncode so a test can tell which call path actually worked."""

    def __init__(self, *, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        # Deliberately does NOT set returncode -- terminate() is ignored.

    def kill(self) -> None:
        self.killed = True
        self.returncode = 137  # conventional "killed" exit code (128 + SIGKILL)


def _start_fake_process(
    pending: list[_FakeProcess],
    started: list[_FakeProcess],
) -> _FakeProcess:
    process = pending.pop(0)
    started.append(process)
    return process


def _config(*, secret_ref: str | None = None) -> EgressConfig:
    return EgressConfig(
        channel_id="gov",
        enabled=True,
        slate_message="CivicCast is preparing the channel.",
        sinks=[
            EgressSinkSpec(
                kind="srt" if secret_ref else "file",
                label="Headend" if secret_ref else "Proof",
                uri="srt://headend.example:9000" if secret_ref else "build/out.ts",
                secret_ref=secret_ref,
            )
        ],
    )


def _source_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "source-a.ts"
    source.write_text("fake", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Council meeting",
                path=str(source),
                duration_seconds=1,
                source_ref="asset-council",
            )
        ],
    )


def _source_plan_with_label(tmp_path: Path, label: str) -> EgressSourcePlan:
    source = tmp_path / f"{label.replace(' ', '-').lower()}.ts"
    source.write_text(label, encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label=label,
                path=str(source),
                duration_seconds=1,
                source_ref=f"asset-{label.replace(' ', '-').lower()}",
            )
        ],
    )


def _live_source_plan(tmp_path: Path, label: str = "Live: Council chamber") -> EgressSourcePlan:
    source = tmp_path / "live-council-chamber.ts"
    source.write_text(label, encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label=label,
                path=str(source),
                duration_seconds=1,
                kind="live",
                source_ref="live-council-chamber",
            )
        ],
    )


def _slate_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "slate.ts"
    source.write_text("slate", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Fallback slate",
                path=str(source),
                duration_seconds=1,
                source_ref="civiccast-slate",
            )
        ],
    )


def _command(action: str = "start") -> EgressCommand:
    return EgressCommand(
        channel_id="gov",
        action=action,  # type: ignore[arg-type]
        issued_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        issued_by="operator",
        command_id=f"cmd-{action}",
    )


def test_start_command_runs_the_channel_session_hook_before_encoding(tmp_path: Path) -> None:
    """A fresh channel start must reset session-scoped caption state."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    seen: list[str] = []
    process = _FakeProcess()

    def _on_start(channel_id: str) -> None:
        seen.append(channel_id)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
        channel_start_hook=_on_start,
    )

    assert daemon.process_once("gov") == 1

    assert seen == ["gov"]


def test_start_command_still_starts_when_the_session_hook_raises(tmp_path: Path) -> None:
    """A failing caption sidecar reset must never block a channel from airing.

    Regression for the unguarded channel_start_hook: the hook performs the
    session-scoped caption sidecar reset, which writes to disk.  If that write
    raises (locked/read-only active.vtt, permissions, disk full), the exception
    propagated out of start-command processing and the channel never reached
    encoding -- a caption concern taking down broadcast.  The hook must be
    best-effort: log the failure and continue with the start.
    """

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    started: list[str] = []
    process = _FakeProcess()

    def _raising_hook(channel_id: str) -> None:
        started.append(channel_id)
        raise OSError("active.vtt is read-only")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
        channel_start_hook=_raising_hook,
    )

    # Must not raise, and the channel must still be started.
    assert daemon.process_once("gov") == 1

    assert started == ["gov"]
    assert process.terminated is False
    state = store.read_state("gov")
    assert state is not None
    assert state.state in {"STARTING", "ON_AIR"}


def test_failed_caption_reset_does_not_read_stale_sidecar(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    reads: list[str] = []

    def reset(channel_id: str) -> None:
        raise OSError("active.vtt is read-only")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _: _source_plan(tmp_path),
        ffmpeg_starter=lambda _: _FakeProcess(),
        channel_start_hook=reset,
        caption_plan_provider=lambda channel: reads.append(channel),
        caption_status_provider=lambda _: "on",
    )
    daemon.process_once("gov")
    assert reads == [], "failed reset must not consult the previous session's caption sidecar"
    assert store.read_state("gov").state == "ON_AIR"
    assert store.recent_health("gov", 1)[0].caption_status == "not-verified"
    assert "captions disabled" in (store.read_state("gov").last_error or "")


@pytest.mark.parametrize("fallback_slate", [False, True])
def test_duplicate_start_on_a_live_process_is_delivered_without_resetting_the_session(
    tmp_path: Path,
    fallback_slate: bool,
) -> None:
    """A distinct second START is delivered as a no-op, not a session reset.

    The store dedupes repeated command ids.  A genuinely distinct second START
    id must therefore be delivered while the first process is still tracked.
    That delivery must NOT run the session-start hook or discard the live
    session's audio: the guard intentionally leaves the existing writer
    running, and the hook's session-scoped cleanup would delete its audio.
    """

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    hook_calls: list[str] = []
    process = _FakeProcess(pid=4242)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None if fallback_slate else _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
        channel_start_hook=hook_calls.append,
    )

    store.enqueue_command(_command())
    assert daemon.process_once("gov") == 1
    assert hook_calls == ["gov"], "the initial start must run the session hook"
    assert store.read_state("gov").pid == 4242

    # A repeated command id is correctly consumed and dropped by the store's
    # dedupe before it can be delivered a second time.
    store.enqueue_command(_command())
    assert store.peek_pending_commands("gov") == []
    assert daemon.process_once("gov") == 0

    before_duplicate = store.read_state("gov")
    assert before_duplicate is not None
    assert before_duplicate.current_proof_event_id is not None

    # A distinct command id reaches the daemon.  The live-process guard makes
    # START a no-op: process identity and hook count must not change.
    second = _command().model_copy(update={"command_id": "cmd-start-2"})
    store.enqueue_command(second)
    assert store.peek_pending_commands("gov") == [second]
    assert daemon.process_once("gov") == 1

    state = store.read_state("gov")
    assert state is not None
    assert state.pid == 4242
    assert state.state == before_duplicate.state
    assert state.current_proof_event_id == before_duplicate.current_proof_event_id
    assert state.current_source_label == before_duplicate.current_source_label
    assert process.terminated is False
    assert hook_calls == ["gov"], (
        "a distinct START on a live tracked process must be delivered but "
        "must not re-run the session-start hook; doing so would discard the "
        "live writer's audio"
    )


def test_daemon_processes_start_command_and_records_success_health(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    captured: dict[str, list[str]] = {}
    process = _FakeProcess()

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda args: captured.setdefault("args", args) and process,
    )

    assert daemon.process_once("gov") == 1

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.pid == 4242
    assert state.current_proof_event_id is not None
    assert captured["args"][-3:] == ["-f", "mpegts", "build/out.ts"]
    assert store.recent_health("gov", 1)[0].sink_connected == {"Proof": True}
    proof_events = store.recent_proof_events("gov", 1)
    assert proof_events[0].source_label == "Council meeting"
    assert proof_events[0].source_ref == "asset-council"
    assert proof_events[0].proof_boundary == "civiccast-egress-handoff-boundary"

    assert daemon.process_once("gov") == 0
    assert store.recent_health("gov", 1)[0].sink_connected == {"Proof": True}


def test_daemon_routes_storage_refusal_to_configured_fallback_slate_before_encoder_start(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    started: list[list[str]] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        caption_readiness_provider=lambda _channel_id: SimpleNamespace(
            ready=False,
            refusal_reason="caption-storage-volumes-diverge",
            requires_fallback_slate=True,
        ),
        ffmpeg_starter=lambda args: started.append(args) or _FakeProcess(),
    )

    assert daemon.process_once("gov") == 1

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert state.last_error == "caption storage refused: caption-storage-volumes-diverge"
    concat_path = Path(next(arg for arg in started[0] if arg.endswith(".ffconcat")))
    assert concat_path.is_file()
    assert (tmp_path / "slate.ts").as_posix() in concat_path.read_text(encoding="utf-8")
    proof_events = store.recent_proof_events("gov", 1)
    assert proof_events[0].source_label == "Fallback slate"
    assert proof_events[0].source_ref == "civiccast-slate"
    assert proof_events[0].proof_boundary == "civiccast-egress-handoff-boundary"


def test_start_does_not_hold_the_pass_on_caption_retention_discovery(tmp_path: Path) -> None:
    """U07: the readiness verdict is CONSULTED, never computed on the start path.

    MEASURED LIVE 2026-09-24 17:19:23 MDT: this same call ran the whole
    retention discovery inline on the automation thread (60.5 s for 15,045
    public chunks) under ``_preparation_guard``, so every other channel's
    command waited behind it and the 30 s automation watchdog fired. With
    discovery blocked here, the pass must still finish and start the program.
    """
    from civiccast.captions.retention import (
        CaptionEvidenceRetentionPolicy,
        CaptionRetentionVerdictSource,
    )
    from civiccast.captions.review import InMemoryCaptionReviewStore

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    started: list[list[str]] = []
    release = Event()
    entered = Event()
    discovered: list[int] = []

    def blocked_discovery(**_kwargs: object) -> list[dict[str, object]]:
        discovered.append(1)
        entered.set()
        release.wait(30.0)
        return []

    storage_root = tmp_path / "egress"
    storage_root.mkdir(parents=True, exist_ok=True)
    policy = CaptionEvidenceRetentionPolicy.from_system(storage_root=storage_root)
    policy._discover_candidates = blocked_discovery  # type: ignore[method-assign]
    provider = CaptionRetentionVerdictSource(
        policy=policy,
        tap_root=None,
        review_store=InMemoryCaptionReviewStore(),
        segment_seconds=5.0,
        storage_root=storage_root,
    )
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        caption_readiness_provider=provider,
        ffmpeg_starter=lambda args: started.append(args) or _FakeProcess(),
    )

    try:
        began = time.monotonic()
        assert daemon.process_once("gov") == 1
        elapsed = time.monotonic() - began
    finally:
        release.set()
        provider.wait_for_sweep(10.0)
        # U08: the first start arms the provider's own refresh timer, so this
        # test must end it -- an armed timer outlives the test otherwise.
        provider.stop(10.0)

    assert elapsed < 1.0, f"the automation pass waited {elapsed:.2f}s on retention discovery"
    assert started, "the program never started"
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    # The real program, not the fallback slate: a verdict that is merely
    # MISSING is not a refusal.
    assert store.recent_proof_events("gov", 1)[0].source_label == "Council meeting"
    assert discovered == [1], "exactly one background sweep for one start"
    assert entered.wait(5.0), "no background sweep was dispatched"


def test_daemon_caption_sender_uses_its_running_encoder_strategy(tmp_path: Path) -> None:
    from civiccast.captions.models import CaptionCue
    from civiccast.egress.caption_feed import CaptionFeedWorker

    class _CaptionStrategy:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def send_caption_cue(
            self,
            channel_id: str,
            work_dir: Path,
            *,
            text: str,
            pts_seconds: float,
            duration_seconds: float,
            delivery_id: str,
            program_token: str | None = None,
        ) -> bool:
            self.calls.append(
                {
                    "channel_id": channel_id,
                    "work_dir": work_dir,
                    "text": text,
                    "pts_seconds": pts_seconds,
                    "duration_seconds": duration_seconds,
                    "delivery_id": delivery_id,
                    "program_token": program_token,
                }
            )
            return True

    strategy = _CaptionStrategy()
    daemon = EgressDaemon(
        InMemoryEgressStore(),
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        encoder_strategy=strategy,  # type: ignore[arg-type]
    )

    assert (
        daemon.send_caption_cue(
            "gov",
            tmp_path,
            text="Council meeting",
            pts_seconds=3.0,
            duration_seconds=1.5,
            delivery_id="caption-page-1",
        )
        is True
    )
    assert strategy.calls == [
        {
            "channel_id": "gov",
            "work_dir": tmp_path,
            "text": "Council meeting",
            "pts_seconds": 3.0,
            "duration_seconds": 1.5,
            "delivery_id": "caption-page-1",
            "program_token": None,
        }
    ]
    # app.py binds the feed to the daemon, rather than directly to the strategy.
    # After a switch this real forwarding seam must retain the program token.
    feed = CaptionFeedWorker(
        work_dir=tmp_path,
        on_air_channels=lambda: ["gov"],
        caption_cue_provider=lambda _channel: [
            CaptionCue(
                cue_id="gov-incoming-1",
                start_seconds=0,
                end_seconds=1,
                text="Incoming program",
                confidence=1,
                low_confidence=False,
            )
        ],
        program_token_provider=lambda _channel: "incoming",
        send_caption_cue=daemon.send_caption_cue,
    )
    assert feed.run_once().cues_sent == 1
    assert strategy.calls[-1]["program_token"] == "incoming"
    assert strategy.calls[-1]["text"] == "Incoming program"


def test_daemon_reports_the_workers_initial_control_connection(tmp_path: Path) -> None:
    class _ReadinessStrategy:
        supports_content_reload = True

        def __init__(self) -> None:
            self.ready = False
            self.calls: list[str] = []

        def worker_initial_control_connection_observed(self, channel_id: str) -> bool:
            self.calls.append(channel_id)
            return self.ready

    strategy = _ReadinessStrategy()
    daemon = EgressDaemon(
        InMemoryEgressStore(),
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        encoder_strategy=strategy,  # type: ignore[arg-type]
    )

    assert daemon.worker_initial_control_connection_observed("education") is False
    strategy.ready = True
    assert daemon.worker_initial_control_connection_observed("education") is True
    assert strategy.calls == ["education", "education"]


def test_daemon_prepares_source_plan_before_starting_encoder(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    raw_plan = _source_plan(tmp_path)
    prepared_path = tmp_path / "prepared.ts"
    prepared_path.write_text("prepared", encoding="utf-8")
    prepared_plan = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Prepared council meeting",
                path=str(prepared_path),
                duration_seconds=1,
            )
        ],
    )
    seen: dict[str, object] = {}

    def prepare(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        seen["source_plan"] = source_plan
        seen["config"] = config
        return SourcePreparationReport(
            source_plan=prepared_plan,
            records=(
                PreparedSegmentRecord(
                    label="Prepared council meeting",
                    source_path=str(raw_plan.segments[0].path),
                    prepared_path=str(prepared_path),
                    loudness_status="ok",
                    measured_lufs=-23.9,
                    normalized=False,
                ),
            ),
        )

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: raw_plan,
        source_preparer=prepare,
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.current_source_label == "Prepared council meeting"
    assert state.current_proof_event_id is not None
    assert store.recent_proof_events("gov", 1)[0].source_path == str(prepared_path)
    assert store.recent_health("gov", 1)[0].last_loudness_lufs == -23.9
    assert seen == {"source_plan": raw_plan, "config": _config()}


def test_daemon_passes_branding_plan_to_encoder_args(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    captured: dict[str, list[str]] = {}
    process = _FakeProcess()
    branding_plan = build_branding_filter_plan(
        overlay_contract=build_overlay_contract(channel_id="gov"),
        snapshot_base_url="http://127.0.0.1:8000",
    )

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        branding_plan_provider=lambda _channel_id: branding_plan,
        ffmpeg_starter=lambda args: captured.setdefault("args", args) and process,
    )

    daemon.process_once("gov")

    assert "-filter_complex" in captured["args"]
    assert branding_plan.filter_complex in captured["args"]
    assert f"[{branding_plan.output_video_label}]" in captured["args"]


def test_daemon_passes_caption_plan_to_encoder_args(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    captured: dict[str, list[str]] = {}
    process = _FakeProcess()
    sidecar = tmp_path / "captions.vtt"
    caption_plan = SidecarCaptionEmbedder(sidecar_path=sidecar).build_plan(
        channel_id="gov",
        cues=[],
    )

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        caption_plan_provider=lambda _channel_id: caption_plan,
        ffmpeg_starter=lambda args: captured.setdefault("args", args) and process,
    )

    daemon.process_once("gov")

    assert str(sidecar) in captured["args"]
    assert "1:s:0?" in captured["args"]
    assert captured["args"].index(str(sidecar)) < captured["args"].index("1:s:0?")


def test_daemon_surfaces_verified_caption_status_from_decode_back_provider(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        caption_status_provider=lambda _channel_id: "on",
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")

    assert store.recent_health("gov", 1)[0].caption_status == "on"


def test_daemon_records_cg_overlay_raise_and_clear_lifecycle(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    active_overlay = build_cg_overlay_egress_proof(
        overlay=build_emergency_overlay(overlay_id="notice-1", severity="warning"),
        overlay_contract=build_overlay_contract(channel_id="gov"),
    )
    overlay_enabled = True

    def overlay_provider(_channel_id: str):
        return active_overlay if overlay_enabled else None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        cg_overlay_proof_provider=overlay_provider,
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")

    overlay_events = [
        event
        for event in store.recent_proof_events("gov", 5)
        if event.proof_boundary == CG_EGRESS_PROOF_BOUNDARY
    ]
    assert len(overlay_events) == 1
    assert overlay_events[0].source_label == "CivicCast emergency banner"
    assert overlay_events[0].source_ref == "notice-1"
    assert "raised emergency banner" in overlay_events[0].machine_summary
    assert "not an EAS claim" in overlay_events[0].machine_summary

    daemon.process_once("gov")

    overlay_events = [
        event
        for event in store.recent_proof_events("gov", 5)
        if event.proof_boundary == CG_EGRESS_PROOF_BOUNDARY
    ]
    assert len(overlay_events) == 1

    overlay_enabled = False
    daemon.process_once("gov")

    overlay_events = [
        event
        for event in store.recent_proof_events("gov", 5)
        if event.proof_boundary == CG_EGRESS_PROOF_BOUNDARY
    ]
    assert [event.source_label for event in overlay_events] == [
        "CivicCast emergency banner cleared",
        "CivicCast emergency banner",
    ]
    assert "cleared emergency banner" in overlay_events[0].machine_summary
    assert "not an EAS claim" in overlay_events[0].machine_summary


def test_daemon_clears_active_cg_overlay_on_stop(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    active_overlay = build_cg_overlay_egress_proof(
        overlay=build_emergency_overlay(overlay_id="notice-1", severity="warning"),
        overlay_contract=build_overlay_contract(channel_id="gov"),
    )
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        cg_overlay_proof_provider=lambda _channel_id: active_overlay,
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")

    overlay_events = [
        event
        for event in store.recent_proof_events("gov", 5)
        if event.proof_boundary == CG_EGRESS_PROOF_BOUNDARY
    ]
    assert [event.source_label for event in overlay_events] == [
        "CivicCast emergency banner cleared",
        "CivicCast emergency banner",
    ]
    assert overlay_events[0].state == "STOPPING"


def _hls_config(folder: Path) -> EgressConfig:
    return EgressConfig(
        channel_id="gov",
        enabled=True,
        slate_message="CivicCast is preparing the channel.",
        sinks=[
            EgressSinkSpec(kind="file", label="Proof", uri="build/out.ts"),
            EgressSinkSpec(kind="hls", label="Web preview (HLS)", uri=str(folder)),
        ],
    )


def test_operator_stop_removes_the_previous_broadcasts_hls_playlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review round 3 delta, MINOR 3: the HLS writers rewrite playlist.m3u8 in
    # place and never remove it, and /api/public/live/current advertises the
    # manifest on the strength of that file existing -- so every start after
    # the first advertised the PREVIOUS broadcast's playlist. The operator stop
    # is where the writer goes away with the channel; the playlist goes too.
    root = tmp_path / "hls-root"
    folder = root / "gov"
    folder.mkdir(parents=True)
    monkeypatch.setenv("CIVICCAST_LIVE_HLS_ROOT", str(root))
    store = InMemoryEgressStore()
    store.upsert_config(_hls_config(folder))
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "work",
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )
    assert daemon.process_once("gov") == 1
    (folder / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (folder / "seg000000001.ts").write_bytes(b"ts")

    store.enqueue_command(_command("stop"))
    assert daemon.process_once("gov") == 1

    assert not (folder / "playlist.m3u8").exists()
    assert (folder / "seg000000001.ts").exists()  # segments are the next writer's business
    state = store.read_state("gov")
    assert state is not None and state.state == "STOPPED"


def test_start_with_no_live_relay_removes_a_stale_hls_playlist_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A daemon that comes up over a folder holding an older broadcast's
    # playlist (unclean death, service restart) must not leave it there for
    # /api/public/live/current to advertise at t=0 of the new pipeline.
    root = tmp_path / "hls-root"
    folder = root / "gov"
    folder.mkdir(parents=True)
    monkeypatch.setenv("CIVICCAST_LIVE_HLS_ROOT", str(root))
    (folder / "playlist.m3u8").write_text("#EXTM3U\n#stale\n", encoding="utf-8")
    store = InMemoryEgressStore()
    store.upsert_config(_hls_config(folder))
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "work",
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    assert daemon.process_once("gov") == 1

    assert not (folder / "playlist.m3u8").exists()
    state = store.read_state("gov")
    assert state is not None and state.state == "ON_AIR"


def test_stale_playlist_removal_never_acts_on_a_cached_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review round 4 delta, MINOR 5: the resolver memo is fine for the public
    # READ paths (the media router re-checks every file it serves), but the
    # daemon's playlist removal WRITES. A resolution cached before the sink
    # folder was swapped for a junction pointing outside the root must not
    # steer an unlink() outside the root. The cache is seeded with exactly
    # that stale state; the removal must re-resolve and touch only the real
    # in-root folder.
    from civiccast.egress import headend

    root = tmp_path / "hls-root"
    folder = root / "gov"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("CIVICCAST_LIVE_HLS_ROOT", str(root))
    (folder / "playlist.m3u8").write_text("#EXTM3U\n#stale\n", encoding="utf-8")
    (outside / "playlist.m3u8").write_text("#EXTM3U\n#not yours\n", encoding="utf-8")
    headend.clear_local_hls_resolution_cache()
    headend.resolve_local_hls_directory(str(folder))
    key = next(iter(headend._resolve_cache))
    headend._resolve_cache[key] = (headend._resolve_cache[key][0], outside.resolve())
    store = InMemoryEgressStore()
    store.upsert_config(_hls_config(folder))
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "work",
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    assert daemon.process_once("gov") == 1

    assert (outside / "playlist.m3u8").is_file(), "unlinked through a stale cached resolution"
    assert not (folder / "playlist.m3u8").exists()


def test_start_leaves_the_playlist_alone_while_the_hls_relay_is_still_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A crash-relaunch of the encoder alone: the relay child keeps writing the
    # same rolling window, so residents' players must not lose the manifest.
    from civiccast.egress.hls_relay import HlsRelaySupervisor

    root = tmp_path / "hls-root"
    folder = root / "gov"
    folder.mkdir(parents=True)
    monkeypatch.setenv("CIVICCAST_LIVE_HLS_ROOT", str(root))
    store = InMemoryEgressStore()
    store.upsert_config(_hls_config(folder))
    store.enqueue_command(_command())
    # U24: the daemon now hands the supervisor its work dir, so a starter
    # double must accept the per-child stderr capture path. This one ignores
    # it: the child is a fake, so there is no child stderr to capture.
    relay = HlsRelaySupervisor(starter=lambda _args, *, stderr_path=None: _FakeProcess(pid=9001))
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "work",
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
        hls_relay_supervisor=relay,
    )
    assert daemon.process_once("gov") == 1
    assert relay.is_alive("gov") is True
    (folder / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    # The encoder is gone but the relay is not: an in-place relaunch.
    daemon._processes["gov"].returncode = 1  # type: ignore[attr-defined]
    daemon.process_once("gov")

    assert (folder / "playlist.m3u8").exists()


def test_daemon_gives_the_relay_its_work_dir_so_child_stderr_lands_beside_the_worker_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U24 end-to-end wiring: the daemon is what knows the station's work dir, so
    # the daemon is what must hand it to the supervisor. Without this the relay
    # capture is inert -- correct in isolation, never pointed anywhere. The
    # asserted directory is the same one ``GstPlayoutStrategy`` writes
    # ``gst-worker.stderr.log`` into (``<work_dir>/<channel>/logs``), which on an
    # installed station is ``C:\ProgramData\CivicCast\data\egress\<channel>\logs``:
    # the incident's relay evidence lands next to the worker's, not somewhere else.
    from civiccast.egress.hls_relay import HlsRelaySupervisor

    root = tmp_path / "hls-root"
    folder = root / "gov"
    folder.mkdir(parents=True)
    monkeypatch.setenv("CIVICCAST_LIVE_HLS_ROOT", str(root))
    store = InMemoryEgressStore()
    store.upsert_config(_hls_config(folder))
    store.enqueue_command(_command())

    work_dir = tmp_path / "work"
    seen: list[Path | None] = []
    argv: list[list[str]] = []

    def relay_starter(args: list[str], *, stderr_path: Path | None = None) -> _FakeProcess:
        argv.append(args)
        seen.append(stderr_path)
        if stderr_path is not None:
            with stderr_path.open("a", encoding="utf-8") as handle:
                handle.write("[mpegts @ 0x1] timestamp discontinuity (stream id=256): -3000000\n")
        return _FakeProcess(pid=9001)

    relay = HlsRelaySupervisor(starter=relay_starter)
    daemon = EgressDaemon(
        store,
        work_dir=work_dir,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
        hls_relay_supervisor=relay,
    )
    assert daemon.process_once("gov") == 1

    # U24: the child's stderr is on disk, in the channel's log directory.
    log_path = work_dir / "gov" / "logs" / "hls-relay.Web_preview_HLS.stderr.log"
    assert seen == [log_path]
    assert log_path.is_file(), f"relay stderr was not captured at {log_path}"
    # ...because the daemon handed the supervisor its work dir: the wiring itself
    # is the assertion, so reading the private field is the point of the test.
    assert relay._log_root == work_dir

    text = log_path.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    # The pid field is fixed-width (10) so it can be stamped in place after the
    # spawn returns, which is why a 4-digit pid reads as ``pid=0000009001``.
    assert header.startswith("[hls-relay] pid=0000009001 ")
    assert " channel=gov " in header
    assert " reason=start " in header
    assert json.loads(header.split(" argv=", 1)[1]) == argv[0]
    # The label is sanitized into the filename, and the child's own line is kept
    # verbatim after the header: this is the evidence the two live incidents lacked.
    assert "timestamp discontinuity" in text


def test_crash_relaunch_rebinds_the_channels_hls_relay_to_the_new_worker_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U21: the relay child corrects input PTS discontinuities itself and keeps the
    # resulting per-stream offset for the rest of its life, so a relaunched worker
    # must not inherit the previous worker's relay. The window on disk is still
    # carried forward (the manifest must survive the relaunch), but the CHILD
    # writing it from here on is a new one.
    from civiccast.egress.hls_relay import HlsRelaySupervisor

    root = tmp_path / "hls-root"
    folder = root / "gov"
    folder.mkdir(parents=True)
    monkeypatch.setenv("CIVICCAST_LIVE_HLS_ROOT", str(root))
    store = InMemoryEgressStore()
    store.upsert_config(_hls_config(folder))
    store.enqueue_command(_command())
    relay_calls: list[list[str]] = []
    relay_procs: list[_FakeProcess] = []

    def relay_starter(args: list[str], *, stderr_path: Path | None = None) -> _FakeProcess:
        relay_calls.append(args)
        proc = _FakeProcess(pid=9000 + len(relay_calls))
        relay_procs.append(proc)
        return proc

    worker_procs: list[_FakeProcess] = []

    def worker_starter(_args: list[str]) -> _FakeProcess:
        proc = _FakeProcess(pid=7000 + len(worker_procs))
        worker_procs.append(proc)
        return proc

    relay = HlsRelaySupervisor(starter=relay_starter)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "work",
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=worker_starter,
        hls_relay_supervisor=relay,
    )
    assert daemon.process_once("gov") == 1
    assert len(relay_calls) == 1
    (folder / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    # The worker dies on its own; the next tick relaunches it.
    daemon._processes["gov"].returncode = 1  # type: ignore[attr-defined]
    daemon.process_once("gov")

    assert len(worker_procs) == 2, "the worker was not relaunched"
    assert len(relay_calls) == 2, "the relaunched worker inherited the previous session's relay"
    assert relay_calls[0] == relay_calls[1], "the rebound relay must keep the same udp port"
    assert relay_procs[0].terminated
    assert not relay_procs[1].terminated
    assert (folder / "playlist.m3u8").exists()


def test_daemon_clears_active_cg_overlay_when_encoder_exits(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    active_overlay = build_cg_overlay_egress_proof(
        overlay=build_emergency_overlay(overlay_id="notice-1", severity="warning"),
        overlay_contract=build_overlay_contract(channel_id="gov"),
    )
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        cg_overlay_proof_provider=lambda _channel_id: active_overlay,
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")
    store.enqueue_command(_command("stop").model_copy(update={"command_id": "cmd-stop-on-exit"}))
    process.returncode = 0
    daemon.process_once("gov")

    overlay_events = [
        event
        for event in store.recent_proof_events("gov", 5)
        if event.proof_boundary == CG_EGRESS_PROOF_BOUNDARY
    ]
    assert [event.source_label for event in overlay_events] == [
        "CivicCast emergency banner cleared",
        "CivicCast emergency banner",
    ]
    assert overlay_events[0].state == "STOPPED"


def test_daemon_writes_fallback_slate_when_no_source_plan(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert "Slate generation is required" in (state.last_error or "")


def test_daemon_logs_last_error_at_info_on_fallback_slate_transition(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Gate A T4 diagnosability fix (2026-09): ``_write_state`` is the ONE
    choke point every pipeline state transition passes through, including
    every ``FALLBACK_SLATE`` entry's ``last_error``. Before this fix the
    control-plane child process had no configured handler for the
    ``civiccast`` logger at any level, so this record was silently dropped
    even though the STATE it describes was durably persisted -- Gate A's T4
    probe found ``engine_state=FALLBACK_SLATE`` with no trail explaining
    why. Proves the daemon actually emits an INFO record naming both the
    new state and the fallback reason (``last_error``), which is the record
    ``configure_control_plane_logging`` now makes reach a file."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
    )

    with caplog.at_level("INFO", logger="civiccast.egress.daemon"):
        daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert state.last_error is not None

    info_records = [r for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "FALLBACK_SLATE" in r.getMessage() and state.last_error in r.getMessage()
        for r in info_records
    ), [r.getMessage() for r in info_records]


@pytest.mark.parametrize("async_warmup", [False, True])
@pytest.mark.parametrize("generated_slate", [False, True])
def test_daemon_runs_fallback_slate_provider_when_no_source_plan(
    tmp_path: Path, generated_slate: bool, async_warmup: bool
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    captured: dict[str, list[str]] = {}
    process = _FakeProcess()
    prepared: list[EgressSourcePlan] = []

    def render(args: list[str]) -> Any:
        Path(args[-1]).write_text("fake canonical TS", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    fallback_plan = (
        SlateSourceGenerator(work_dir=tmp_path, ffmpeg_runner=render)(_config())
        if generated_slate
        else _slate_plan(tmp_path)
    )
    expected_label = fallback_plan.segments[0].label

    def prepare(plan: EgressSourcePlan, _config: EgressConfig) -> SourcePreparationReport:
        prepared.append(plan)
        return SourcePreparationReport(source_plan=plan, records=())

    warmed = []
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        fallback_source_provider=lambda _config: fallback_plan,
        source_preparer=prepare,
        channel_caption_prepare_hook=lambda channel, cancel: warmed.append(channel),
        ffmpeg_starter=lambda args: captured.setdefault("args", args) and process,
    )

    if async_warmup:
        daemon.enable_async_preparation()
    try:
        daemon.process_once("gov")
        if "gov" in daemon._preparations:
            daemon._preparations["gov"].future.result(timeout=5)
            daemon.process_once("gov")
    finally:
        daemon.shutdown_preparation()
    assert warmed == (["gov"] if async_warmup and not generated_slate else [])

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert state.current_source_label == expected_label
    assert state.pid == process.pid
    assert prepared == ([] if generated_slate else [fallback_plan])
    assert daemon.live_prepared_plan_dirs("gov") == frozenset()
    assert state.current_proof_event_id is not None
    assert any("egress-source-plan.ffconcat" in arg for arg in captured["args"])
    proof_event = store.recent_proof_events("gov", 1)[0]
    assert proof_event.state == "FALLBACK_SLATE"
    assert proof_event.source_label == expected_label
    assert "entered fallback slate" in proof_event.machine_summary
    assert store.recent_health("gov", 1)[0].state == "FALLBACK_SLATE"

    daemon.process_once("gov")

    state_after_poll = store.read_state("gov")
    assert state_after_poll is not None
    assert state_after_poll.state == "FALLBACK_SLATE"
    assert state_after_poll.current_proof_event_id == proof_event.event_id


def test_daemon_airs_fallback_slate_when_source_provider_fails(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: (_ for _ in ()).throw(
            SourcePrepareError("Scheduled asset is missing.")
        ),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert state.current_source_label == "Fallback slate"
    assert state.last_error == "Scheduled asset is missing."
    assert store.recent_proof_events("gov", 1)[0].state == "FALLBACK_SLATE"


def test_daemon_airs_fallback_slate_when_source_preparation_fails(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        source_preparer=lambda _source_plan, _config: (_ for _ in ()).throw(
            SourcePrepareError("Program asset could not be conformed.")
        ),
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert state.current_source_label == "Fallback slate"
    assert state.last_error == "Program asset could not be conformed."
    assert store.recent_health("gov", 1)[0].state == "FALLBACK_SLATE"


def test_daemon_records_fallback_slate_exit_on_reload(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    source_available = False
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []

    def source_provider(_channel_id: str) -> EgressSourcePlan | None:
        return _source_plan(tmp_path) if source_available else None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    daemon.process_once("gov")
    daemon.record_rollover_plan_end(
        "gov", datetime.now(UTC) + timedelta(minutes=5), command_id="cmd-reload"
    )
    store.enqueue_command(_command("reload"))
    source_available = True
    daemon.process_once("gov")
    started[0].returncode = 0

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Council meeting"
    proof_events = store.recent_proof_events("gov", 3)
    assert proof_events[0].state == "ON_AIR"
    assert "exited fallback slate" in proof_events[0].machine_summary
    assert proof_events[1].state == "TRANSITIONING"
    assert "from 'Fallback slate' to 'Council meeting'" in proof_events[1].machine_summary
    assert proof_events[2].state == "FALLBACK_SLATE"
    assert processes == []


def test_daemon_records_source_to_source_transition_on_reload(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        source = tmp_path / f"{current_label.replace(' ', '-').lower()}.ts"
        source.write_text(current_label, encoding="utf-8")
        return EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(label=current_label, path=str(source), duration_seconds=1)
            ],
        )

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    transition_state = store.read_state("gov")
    assert transition_state is not None
    assert transition_state.state == "TRANSITIONING"
    assert started[0].terminated is False

    started[0].returncode = 0
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"
    proof_events = store.recent_proof_events("gov", 3)
    assert [event.state for event in proof_events] == ["ON_AIR", "TRANSITIONING", "ON_AIR"]
    assert proof_events[0].source_label == "Mayor interview"
    assert proof_events[1].source_label == "Mayor interview"
    assert "from 'Council meeting' to 'Mayor interview'" in proof_events[1].machine_summary


class _FakeContentReloadStrategy:
    """A content-reload-capable strategy (stands in for GstPlayoutStrategy) so the
    daemon's seamless-reload routing can be exercised without gi (S15 / D-S1-6)."""

    name = "fake-gst-content-reload"
    supports_live_swap = True
    supports_content_reload = True

    def __init__(
        self,
        processes: list[_FakeProcess],
        started: list[_FakeProcess],
        *,
        reload_ok: bool = True,
        reload_exc: Exception | None = None,
        # F1/F9: False lets a test control settlement timing itself (simulate
        # a deferred switch that stays pending across several poll ticks)
        # instead of the fake auto-settling "applied" the instant it arms.
        auto_settle: bool = True,
    ) -> None:
        self._processes = processes
        self._started = started
        self._reload_ok = reload_ok
        self._reload_exc = reload_exc
        self._auto_settle = auto_settle
        self.reload_calls: list[str] = []
        self.reload_ids: list[str | None] = []
        # B3 fix: records what each reload_content call was asked for, so tests
        # can pin daemon.py's should_defer_switch wiring (_try_content_reload
        # sets EncoderStartRequest.switch_at_end_of_current).
        self.switch_at_end_of_current_calls: list[bool] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        process = self._processes.pop(0)
        self._started.append(process)
        return EncoderStartResult(
            process=process,
            concat_plan_path=request.work_dir / "playout-graph.json",
            stdout_path=request.work_dir / "out.log",
            stderr_path=request.work_dir / "err.log",
            args=("worker",),
        )

    def swap_role(self, channel_id: str, work_dir: Path, role: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def reload_content(
        self,
        channel_id: str,
        work_dir: Path,
        request: EncoderStartRequest,
        *,
        command_id: str | None = None,
    ) -> bool:
        self.reload_calls.append(request.source_plan.segments[0].label)
        self.reload_ids.append(command_id)
        self.switch_at_end_of_current_calls.append(request.switch_at_end_of_current)
        if self._reload_exc is not None:
            raise self._reload_exc
        if self._reload_ok and self._auto_settle:
            # F1 redesign: True now means ARMED, and the daemon defers its
            # ON_AIR bookkeeping to _poll_reload_settlement observing
            # reload-status.json -- this fake simulates an immediate,
            # instantly-settling reload (the common case: switch_at_end_of_
            # current=False commits on the new leg's first buffer) by writing
            # that file right away. A test that needs to see the daemon's
            # POST-settlement state therefore calls process_once() ONE MORE
            # TIME after the reload -- the same shape a real deferred switch
            # takes, just compressed to zero wall-clock delay. A test that
            # constructs this fake with auto_settle=False controls the status
            # file itself (F9: proving the daemon does not terminate the
            # worker while genuinely still awaiting settlement).
            _write_fake_reload_status(work_dir, channel_id, command_id, "applied")
        return self._reload_ok


class _FakeNonReloadCapableStrategy(_FakeContentReloadStrategy):
    """Identical to ``_FakeContentReloadStrategy`` except it does NOT declare
    ``supports_content_reload`` -- ``_request_reload``'s own guard
    (``getattr(self._encoder_strategy, "supports_content_reload", False)``)
    must short-circuit to the restart path before ``_try_content_reload`` is
    ever called, so ``reload_calls`` staying empty is the proof."""

    supports_content_reload = False


def test_failed_caption_reset_gate_survives_reload_until_successful_start(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    started: list[_FakeProcess] = []
    requests: list[EncoderStartRequest] = []
    sends: list[str] = []
    reset_fails = [True]

    class Strategy(_FakeContentReloadStrategy):
        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            requests.append(request)
            return super().start(request)

        def reload_content(self, channel_id, work_dir, request, *, command_id=None):
            requests.append(request)
            return super().reload_content(channel_id, work_dir, request, command_id=command_id)

        def send_caption_cue(self, channel_id, work_dir, **kwargs):
            sends.append(kwargs["text"])
            return True

    def reset(channel_id: str) -> None:
        if reset_fails[0]:
            raise OSError("locked sidecar")

    strategy = Strategy([_FakeProcess(pid=111), _FakeProcess(pid=222)], started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        encoder_strategy=strategy,
        source_plan_provider=lambda _: _source_plan(tmp_path),
        channel_start_hook=reset,
    )
    store.enqueue_command(_command())
    daemon.process_once("gov")
    assert not requests[-1].captions_allowed
    assert requests[-1].caption_plan is None
    assert requests[-1].audio_tap_plan is None
    assert not daemon.send_caption_cue(
        "gov", tmp_path, text="OLD", pts_seconds=0, duration_seconds=1, delivery_id="old"
    )
    assert sends == []
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")
    assert len(requests) == 2
    assert not requests[-1].captions_allowed
    assert not started[0].terminated
    reset_fails[0] = False
    # A duplicate Start on a live worker is not a reset and cannot lift the gate.
    store.enqueue_command(_command().model_copy(update={"command_id": "duplicate-start"}))
    daemon.process_once("gov")
    assert "gov" in daemon._caption_reset_failed
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")
    store.enqueue_command(_command().model_copy(update={"command_id": "fresh-start"}))
    daemon.process_once("gov")
    assert requests[-1].captions_allowed
    assert "gov" not in daemon._caption_reset_failed
    assert daemon.send_caption_cue(
        "gov", tmp_path, text="NEW", pts_seconds=0, duration_seconds=1, delivery_id="new"
    )
    assert sends == ["NEW"]


def test_content_reload_swaps_program_in_place_without_restart(tmp_path: Path) -> None:
    """D-S1-6: a content-reload-capable strategy applies a newly-due program in
    place — no TRANSITIONING, no second process, the running encoder is untouched —
    and the proof chain still records the source-to-source transition."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    # seamless: the worker stayed up, no second encoder was started, no kill
    assert strategy.reload_calls == ["Mayor interview"]
    assert len(started) == 1
    assert started[0].terminated is False
    assert len(processes) == 1 and processes[0].pid == 222  # the spare was never started

    # F1 redesign: the reload is ARMED after the process_once above (the fake
    # strategy already wrote reload-status.json "applied"), but the daemon's
    # ON_AIR bookkeeping only lands once _poll_reload_settlement observes it --
    # one more tick, exactly like a real deferred switch settling later.
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"
    # the live state never left ON_AIR (output never went down), but the proof chain
    # still records the source-to-source transition for parity with the restart path
    proof_events = store.recent_proof_events("gov", 3)
    assert [event.state for event in proof_events] == ["ON_AIR", "TRANSITIONING", "ON_AIR"]
    assert proof_events[0].source_label == "Mayor interview"
    assert "from 'Council meeting' to 'Mayor interview'" in proof_events[1].machine_summary


class _SpyPipeChannel:
    """Structural stand-in for the D2 ``_WindowsPipeChannel`` (mirrors
    ``tests/egress/test_gst_strategy.py``'s ``_FakePipeChannel``) so a test can
    construct the REAL ``GstPlayoutStrategy`` -- not the ``_FakeContentReloadStrategy``
    double used elsewhere in this file -- and still exercise its ``os.name=='nt'``
    branch platform-agnostically. Records every verb sent so the daemon's routing
    can be proven from the outside, independent of the strategy's own bookkeeping."""

    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id
        self.sent: list[tuple[str, str, str | None]] = []

    def start(self) -> None:
        pass

    def send_and_wait(self, verb: str, line: str, *, command_id: str | None = None) -> bool:
        self.sent.append((verb, line, command_id))
        return True

    def close(self) -> None:
        pass


def test_real_strategy_default_takes_the_seamless_reload_path_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beta.5 gate (owner decision 2026-09-06), proven end-to-end through the
    daemon rather than in isolation: with ``CIVICCAST_EGRESS_SEAMLESS_RELOAD``
    unset, a REAL ``GstPlayoutStrategy`` (not the ``_FakeContentReloadStrategy``
    test double every other reload test in this file uses) must default
    ``supports_content_reload`` to True, and a plan rollover through
    ``EgressDaemon`` must actually enter ``_try_content_reload`` and reach the
    strategy's real ``reload_content`` -> ``send_command`` path -- proved here
    via a spy on the D2 pipe channel's ``send_and_wait`` -- rather than falling
    back to terminate+restart. ``tests/egress/test_gst_strategy.py`` separately
    covers ``supports_content_reload``'s default/override matrix in isolation;
    this test proves the daemon actually takes that path, not just that the
    flag reads True."""
    monkeypatch.delenv("CIVICCAST_EGRESS_SEAMLESS_RELOAD", raising=False)
    pending = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    channels: dict[str, _SpyPipeChannel] = {}

    def pipe_channel_factory(channel_id: str) -> _SpyPipeChannel:
        channel = _SpyPipeChannel(channel_id)
        channels[channel_id] = channel
        return channel

    strategy = GstPlayoutStrategy(
        worker_launcher=lambda argv, stdout, stderr: _start_fake_process(pending, started),
        pipe_channel_factory=pipe_channel_factory,  # type: ignore[arg-type]
        is_windows=True,  # exercises the D2 pipe path platform-agnostically
        encoder_probe=lambda _factory: False,  # no physical GPU/gi in this pipe-path fixture
    )
    assert strategy.supports_content_reload is True  # sanity: the default this test relies on

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")  # _start -> worker up
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # a due plan rollover: must enter _try_content_reload

    channel = channels["gov"]
    assert [verb for verb, _line, _cid in channel.sent] == ["reload"]
    assert len(started) == 1  # no second worker was started -- no restart happened


def test_content_reload_defers_switch_for_an_on_air_reload_with_no_override(
    tmp_path: Path,
) -> None:
    """B3 fix: an ON_AIR reload with no operator override active (the shape
    channel-automation's plan-rollover reload takes) asks the strategy to
    defer the selector switch to the outgoing leg's own EOS -- see
    ``reload_policy.should_defer_switch``."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")  # initial start -> ON_AIR
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.switch_at_end_of_current_calls == [True]


@pytest.mark.parametrize("generated_slate", [False, True])
def test_horizon_rollover_from_terminal_schedule_settles_as_fallback_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generated_slate: bool,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)
    prepared: list[str] = []

    def render(args: list[str]) -> Any:
        Path(args[-1]).write_text("fake canonical TS", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    fallback_plan = (
        SlateSourceGenerator(work_dir=tmp_path, ffmpeg_runner=render)(_config())
        if generated_slate
        else _source_plan_with_label(tmp_path, "CivicCast filler")
    )
    fallback_label = fallback_plan.segments[0].label

    def prepare(plan: EgressSourcePlan, _config: EgressConfig) -> SourcePreparationReport:
        prepared.append(plan.segments[0].label)
        return SourcePreparationReport(source_plan=plan, records=())

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Terminal program"),
        fallback_source_provider=lambda _config: fallback_plan,
        encoder_strategy=strategy,
        source_preparer=prepare,
    )

    daemon.process_once("gov")
    daemon.record_rollover_plan_end(
        "gov",
        datetime.now(UTC) + timedelta(minutes=5),
        command_id="cmd-reload",
        force_fallback=True,
    )
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert prepared == ["Terminal program"] + ([] if generated_slate else [fallback_label])
    assert strategy.reload_calls == [fallback_label]
    assert strategy.switch_at_end_of_current_calls == [True]
    assert len(started) == 1

    sink_states: list[str] = []
    alert_states: list[str] = []
    original_sink_connected = daemon._sink_connected

    def record_sink_state(*args: Any, **kwargs: Any) -> dict[str, bool]:
        sink_states.append(kwargs["state"])
        return original_sink_connected(*args, **kwargs)

    monkeypatch.setattr(daemon, "_sink_connected", record_sink_state)
    monkeypatch.setattr(
        daemon,
        "_alert_evaluator_hook",
        lambda _channel, state, _fps, _bitrate: alert_states.append(state),
    )
    # Inspect the settlement itself: a later ordinary poll would overwrite
    # the inconsistent immediate sample and hide the regression.
    daemon._poll_reload_settlement("gov")
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert state.current_source_label == fallback_label
    assert state.pid == 111
    assert store.recent_health("gov", 1)[0].state == "FALLBACK_SLATE"
    assert sink_states == ["FALLBACK_SLATE"]
    assert alert_states == ["FALLBACK_SLATE"]
    assert [event.state for event in store.recent_proof_events("gov", 3)] == [
        "FALLBACK_SLATE",
        "TRANSITIONING",
        "ON_AIR",
    ]


def test_horizon_rollover_honors_schedule_published_before_command_drain(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Terminal program"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        plan = _source_plan_with_label(tmp_path, current_label)
        if current_label != "Terminal program":
            plan.segments[0] = plan.segments[0].model_copy(
                update={"source_ref": "asset-terminal-program"}
            )
        return plan

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=lambda _config: _source_plan_with_label(
            tmp_path, "CivicCast filler"
        ),
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")
    daemon.record_rollover_plan_end(
        "gov",
        datetime.now(UTC) + timedelta(milliseconds=100),
        command_id="cmd-reload",
        force_fallback=True,
    )
    current_label = "Late published council meeting"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == ["Late published council meeting"]
    assert strategy.switch_at_end_of_current_calls == [True]
    assert len(started) == 1


def test_horizon_rollover_does_not_treat_provider_clock_jitter_as_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)
    base = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    provider_returned = {"value": False}

    class _JitteringDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return base if not provider_returned["value"] else base + timedelta(seconds=10)

    def terminal_provider(_channel_id: str) -> EgressSourcePlan:
        plan = _source_plan_with_label(tmp_path, "Terminal program")
        provider_returned["value"] = True
        return plan

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=terminal_provider,
        fallback_source_provider=lambda _config: _source_plan_with_label(
            tmp_path, "CivicCast filler"
        ),
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")
    provider_returned["value"] = False
    monkeypatch.setattr("civiccast.egress.daemon.datetime", _JitteringDatetime)
    daemon.record_rollover_plan_end(
        "gov", base + timedelta(seconds=1), command_id="cmd-reload", force_fallback=True
    )
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == ["CivicCast filler"]


def test_horizon_rollover_rejects_a_shrinking_schedule_suffix(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    phase = {"terminal": False}

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        first = _source_plan_with_label(tmp_path, "Asset A").segments[0]
        second = _source_plan_with_label(tmp_path, "Asset B").segments[0]
        return EgressSourcePlan(
            channel_id="gov", segments=[second] if phase["terminal"] else [first, second]
        )

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=lambda _config: _source_plan_with_label(
            tmp_path, "CivicCast filler"
        ),
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")
    phase["terminal"] = True
    daemon.record_rollover_plan_end(
        "gov",
        datetime.now(UTC) + timedelta(seconds=1),
        command_id="cmd-reload",
        force_fallback=True,
    )
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == ["CivicCast filler"]


def test_failed_rollover_filler_keeps_current_worker_on_air(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)

    def failed_filler(_config: EgressConfig) -> EgressSourcePlan:
        raise SourcePrepareError("Filler could not be prepared.")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Terminal program"),
        fallback_source_provider=failed_filler,
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")
    daemon.record_rollover_plan_end(
        "gov",
        datetime.now(UTC) + timedelta(minutes=5),
        command_id="cmd-reload",
        force_fallback=True,
    )
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == []
    assert len(started) == 1
    assert started[0].terminated is False
    assert store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]


def test_content_reload_cuts_immediately_when_the_recorded_rollover_horizon_has_already_passed(
    tmp_path: Path,
) -> None:
    """Item 78 fix 3: ``ChannelAutomationService`` records the ``plan_end_at`` it
    computed for a rollover reload (``record_rollover_plan_end``) before
    enqueuing it. If that horizon has already passed by the time the reload
    actually dispatches (e.g. the automation pass itself blocked for a long
    time before reaching this channel), deferring the switch to the outgoing
    leg's own EOS is deferring to a boundary that already happened and will
    never arrive -- the switch must cut in immediately instead, even though
    this is otherwise the exact B3 "ON_AIR, no override" shape that would
    normally defer (see the test above)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")  # initial start -> ON_AIR
    current_label = "Mayor interview"
    # Scoped to the SAME command_id "cmd-reload" the reload below carries
    # (round 8: scoping is now mandatory -- see record_rollover_plan_end's
    # docstring), so _request_reload's match succeeds and the value is used.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )  # already long past
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.switch_at_end_of_current_calls == [False]


def test_record_rollover_plan_end_is_consumed_once_and_never_leaks_to_a_later_reload(
    tmp_path: Path,
) -> None:
    """Item 78 fix 3: the recorded horizon is popped (not merely read) by
    ``_try_content_reload`` -- a stale value from an earlier, already-settled
    rollover must never silently apply to a LATER, unrelated reload of the
    same channel (e.g. a second automation-driven ON_AIR extension with no
    override, which should defer normally)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222), _FakeProcess(pid=333)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")  # initial start -> ON_AIR
    current_label = "Mayor interview"
    # Scoped to "cmd-reload", the id of the reload command enqueued right
    # after (round 8: scoping is mandatory).
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )  # already long past
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")
    assert strategy.switch_at_end_of_current_calls == [False]
    # F1 redesign: the first reload is ARMED, not yet settled -- one more
    # tick lets _poll_reload_settlement observe reload-status.json and
    # finish the ON_AIR bookkeeping before a second reload is requested
    # (mirrors test_content_reload_swaps_program_in_place_without_restart
    # above).
    daemon.process_once("gov")

    current_label = "Second Program"
    # A distinct command_id -- `_command("reload")` always returns the SAME
    # id, and the store treats re-enqueuing an already-consumed id as a
    # no-op (see the identical pattern elsewhere in this file).
    store.enqueue_command(_command("reload").model_copy(update={"command_id": "cmd-reload-2"}))
    daemon.process_once("gov")

    # The stale plan_end_at from the FIRST reload must not still apply here --
    # this second reload, with nothing recorded for it, defers normally.
    assert strategy.switch_at_end_of_current_calls == [False, True]


def test_content_reload_disabled_config_pops_the_recorded_plan_end_without_using_it(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 2, item 3: ``record_rollover_plan_end``'s value
    used to be popped only at the point ``EncoderStartRequest`` was actually
    built -- every early return ABOVE that point (disabled config, a
    SourcePrepareError from the provider or the preparer, a foreign/None
    plan) left it sitting in ``_rollover_plan_end_at``, where it would
    silently apply to whatever reload for this channel came next, however
    unrelated. It must now be consumed (popped) at the very top of
    ``_try_content_reload``, before any of those early returns -- this test
    covers the disabled-config path."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Scoped to "cmd-reload" so _request_reload's match succeeds (round 8:
    # a mismatch would otherwise leave the entry in place, not popped).
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )
    store.upsert_config(_config().model_copy(update={"enabled": False}))
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == []  # never reached reload_content
    assert daemon._rollover_plan_end_at == {}  # popped, not left leaking


def test_content_reload_source_prepare_error_from_provider_pops_the_recorded_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 2, item 3 -- the provider-raises-
    SourcePrepareError early-return path."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"
    fail_next = False

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        if fail_next:
            raise SourcePrepareError("Scheduled asset is missing.")
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Scoped to "cmd-reload" so _request_reload's match succeeds.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )
    fail_next = True
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == []
    assert daemon._rollover_plan_end_at == {}


def test_content_reload_source_prepare_error_from_preparer_pops_the_recorded_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 2, item 3 -- the preparer-raises-
    SourcePrepareError early-return path (a cold-conform failure, distinct
    from the provider-level failure above)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"
    fail_next = False

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    def source_preparer(source_plan: EgressSourcePlan, _config: EgressConfig) -> object:
        if fail_next:
            raise SourcePrepareError("Program asset could not be conformed.")
        return SimpleNamespace(source_plan=source_plan, plan_dir=None, records=[])

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        source_preparer=source_preparer,
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Scoped to "cmd-reload" so _request_reload's match succeeds.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )
    fail_next = True
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == []
    assert daemon._rollover_plan_end_at == {}


def test_content_reload_foreign_channel_plan_pops_the_recorded_plan_end(tmp_path: Path) -> None:
    """Coordinator review, round 2, item 3 -- the foreign/mismatched-channel
    plan early-return path (mirrors
    test_content_reload_foreign_channel_plan_falls_back_to_restart below)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    foreign = False

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        plan = _source_plan_with_label(
            tmp_path, "Mayor interview" if foreign else "Council meeting"
        )
        if foreign:
            object.__setattr__(plan, "channel_id", "someone-else")
        return plan

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Scoped to "cmd-reload" so _request_reload's match succeeds.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )
    foreign = True
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == []  # foreign plan rejected before reload_content
    assert daemon._rollover_plan_end_at == {}


def test_request_reload_worker_missing_or_dead_pops_the_recorded_plan_end(tmp_path: Path) -> None:
    """Coordinator review, round 3, item A: ``_request_reload`` has THREE
    early returns of its own that never reach ``_try_content_reload`` at
    all -- round 2 only closed the leak paths INSIDE that method. This one
    is "the worker is missing or has exited" (``_request_reload`` routes to
    ``_start`` instead, and never calls ``_try_content_reload``)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Scoped to "cmd-reload" so _request_reload's match succeeds.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )
    started[0].returncode = 0  # the worker died
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == ["Mayor interview"]  # recovery restored a live worker first
    assert len(started) == 2  # restarted instead
    assert daemon._rollover_plan_end_at == {}


def test_request_reload_no_state_row_pops_the_recorded_plan_end(tmp_path: Path) -> None:
    """Coordinator review, round 3, item A -- "no state row" (the ``state is
    not None and ...`` guard short-circuits before ``_try_content_reload``
    is ever called, falling straight to the terminate+restart path).

    Calls ``_request_reload`` directly (rather than via ``process_once``'s
    command loop): ``process_once`` runs ``_poll_process`` on every tick
    BEFORE draining commands, and ``_poll_process`` re-establishes a state
    row for any channel with a live process (deriving one from whatever the
    current health/draining/pending-reload bookkeeping says) -- which would
    mask "no state row" the instant a real "reload" command was queued
    alongside it. A genuinely missing state row against a live, tracked
    process is exactly what this guard exists to handle regardless."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    processes: list[_FakeProcess] = []
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Council meeting"),
        encoder_strategy=strategy,
    )
    daemon._processes["gov"] = _FakeProcess(pid=111)  # type: ignore[attr-defined]
    # Deliberately no store.write_state call -- no state row exists at all.

    # ``_request_reload("gov")`` below is a direct call with no command_id
    # (defaults to ``None``, matching supervisor.py's own direct-call
    # shape) -- record with the same explicit ``None`` so the match
    # succeeds and the pop still happens (round 8: ``command_id`` is now
    # required, so an unscoped recording must say so explicitly).
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    daemon._request_reload("gov")  # type: ignore[attr-defined]

    assert strategy.reload_calls == []  # _try_content_reload was never reached
    assert daemon._rollover_plan_end_at == {}


def test_request_reload_strategy_without_support_pops_the_recorded_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 3, item A -- the strategy doesn't declare
    ``supports_content_reload`` (``_request_reload``'s ``getattr`` guard
    short-circuits before ``_try_content_reload`` is ever called)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeNonReloadCapableStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Scoped to "cmd-reload" so _request_reload's match succeeds.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == []  # _try_content_reload was never reached
    assert daemon._rollover_plan_end_at == {}


def test_request_reload_after_a_same_tick_crash_relaunch_still_sees_the_recorded_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 4, item 1 -- REGRESSION in b4508ef (round 3).

    Item 78's own diagnosed scenario is a worker crash whose relaunch
    (``_poll_process`` -> ``_relaunch_after_crash`` -> ``_begin_relaunch`` ->
    ``_start``) runs BEFORE that same ``process_once`` tick's queued
    "reload" command is drained (``process_once`` runs its poll tuple,
    ``_poll_process`` included, before ``pop_pending_commands``). A round-3
    revision popped ``self._rollover_plan_end_at`` inside ``_start`` too
    (defense-in-depth, or so it seemed) -- but ``_start``'s pop ran FIRST,
    during the poll phase, and ate the value ``_request_reload``'s own pop
    needed moments later during command draining: MEASURED, ff5cdfb (round
    2) cut immediately here as it should; b4508ef (round 3) silently
    deferred instead (an up-to-900s held leg on a switch that should have
    cut immediately). Only ``_request_reload`` and ``_stop`` may ever pop
    this dict now; ``_start`` must not."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR (process 111)

    # Automation recorded a rollover plan_end that has already passed by the
    # time this reload actually dispatches -- should_defer_switch must cut
    # immediately for it, never defer. Scoped to "cmd-reload" so
    # _request_reload's match succeeds.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-reload"
    )
    current_label = "Mayor interview"
    started[0].returncode = 1  # the worker crashes
    store.enqueue_command(_command("reload"))
    # ONE process_once tick: _poll_process observes the crash and relaunches
    # (a fresh process 222, via _start) BEFORE the queued "reload" command
    # drains and reaches _request_reload / _try_content_reload.
    daemon.process_once("gov")

    assert len(started) == 2  # the crash-relaunch really did happen first
    assert strategy.reload_calls == ["Mayor interview"]  # the seamless path still ran
    assert strategy.switch_at_end_of_current_calls == [False]  # cut immediately, not deferred
    assert daemon._rollover_plan_end_at == {}


def test_stop_clears_the_recorded_rollover_plan_end(tmp_path: Path) -> None:
    """Coordinator review, round 4, item 2(b): ``_stop`` must clear
    ``_rollover_plan_end_at`` too (the channel going dark makes any in-
    flight rollover moot) -- a test that FAILS if that pop is reverted,
    unlike the suite as a whole (nothing else exercises this path)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Council meeting"),
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # command_id=None: unscoped, deliberately -- ``_stop``'s own pop (unlike
    # ``_request_reload``'s) is unconditional regardless of any command_id,
    # so the matching rule under test elsewhere is irrelevant here.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    assert daemon._rollover_plan_end_at == {
        "gov": (None, datetime(2020, 1, 1, tzinfo=UTC), False, None)
    }

    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")

    assert daemon._rollover_plan_end_at == {}


def test_worker_clean_exit_then_restart_then_plain_reload_does_not_leak_stale_rollover_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 5, item 1 -- REGRESSION left by round 4.

    Round 4's own docstring claimed a stale ``_rollover_plan_end_at`` entry
    could never reach ``should_defer_switch`` because ``_request_reload``
    pops it first -- false: the pop PASSES the value down (as
    ``rollover_plan_end_at=``) into ``_try_content_reload`` and from there
    into ``should_defer_switch``, it does not discard it. A worker that
    exits cleanly (rc=0, no pending reload) never reaches ``_stop`` --
    that route is ``_poll_process`` observing the exit on its own, not an
    operator stop or a drain -- so the entry survived across the channel
    going fully off-air (``STOPPED``) and being freshly restarted
    (``ON_AIR``). MEASURED: record a rollover plan_end already in the past,
    let the worker exit cleanly, restart the channel, then issue a PLAIN
    operator reload with no rollover behind it at all -- the reload wrongly
    saw the stale, already-past ``plan_end_at`` and cut immediately
    (``switch_at_end_of_current=False``) instead of deferring normally
    (``True``, the ordinary ON_AIR/no-override shape an unrecorded reload
    should take). Fixed by clearing the entry in the clean-exit branch of
    ``_poll_process`` itself, alongside the terminal-``ERROR`` branch,
    ``_drain``'s process-is-None branch, and ``stop_all_channels``'
    already-gone branch (see the other new tests below and
    ``_rollover_plan_end_at``'s docstring)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Council meeting"),
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")  # initial start -> ON_AIR (process 111)

    # command_id=None: unscoped -- the clean-exit branch's own pop (like
    # ``_stop``'s) is unconditional, so no id needs to match here.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    store.enqueue_command(_command("stop").model_copy(update={"command_id": "cmd-stop-on-exit"}))
    started[0].returncode = 0  # a clean exit, e.g. the channel's own plan ran out
    daemon.process_once("gov")  # _poll_process's clean-exit branch -> STOPPED

    assert store.read_state("gov").state == "STOPPED"
    assert daemon._rollover_plan_end_at == {}  # must not survive the channel going dark

    # Fresh command_ids -- the initial "cmd-start"/"cmd-reload" ids from
    # ``_command()`` are already consumed and would be silently deduped.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-restart",
        )
    )
    daemon.process_once("gov")  # restart -> ON_AIR (process 222)
    assert store.read_state("gov").state == "ON_AIR"

    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 14, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-plain-reload",
        )
    )  # a plain reload, no rollover behind it
    daemon.process_once("gov")

    assert strategy.switch_at_end_of_current_calls == [True]  # deferred, not cut
    assert daemon._rollover_plan_end_at == {}


def test_poll_process_terminal_error_clears_the_recorded_rollover_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 5, item 1: the terminal-``ERROR`` branch of
    ``_poll_process`` (a crash the daemon does not relaunch, because the
    state row is not in one of the relaunchable states) is another route
    that takes the channel off-air without ever reaching ``_stop`` --
    it must clear the entry itself too."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess(pid=111)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        ffmpeg_starter=lambda args: process,
    )
    daemon.process_once("gov")  # start -> ON_AIR

    # command_id=None: unscoped -- this off-air branch's pop is unconditional.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    # Force the state row out of every relaunchable state so the crash below
    # lands in the terminal ERROR branch instead of _relaunch_after_crash.
    store.write_state(
        EgressStateRow(channel_id="gov", state="STOPPED", updated_at=datetime.now(UTC))
    )
    process.returncode = 1  # a crash the daemon will not relaunch
    daemon.process_once("gov")

    assert store.read_state("gov").state == "ERROR"
    assert daemon._rollover_plan_end_at == {}


def test_drain_with_no_live_process_clears_the_recorded_rollover_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 5, item 1: ``_drain``'s process-is-None
    branch (drain issued against a channel with no live process at all) is
    another off-air route that bypasses ``_stop`` entirely -- it must
    clear the entry itself too."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=lambda _cid: _source_plan(tmp_path)
    )

    # command_id=None: unscoped -- ``_drain``'s process-is-None pop is
    # unconditional, so no id needs to match here.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    assert daemon._rollover_plan_end_at == {
        "gov": (None, datetime(2020, 1, 1, tzinfo=UTC), False, None)
    }

    store.enqueue_command(_command("drain"))
    daemon.process_once("gov")  # _drain: process is None -> STOPPED

    assert store.read_state("gov").state == "STOPPED"
    assert daemon._rollover_plan_end_at == {}


def test_stop_all_channels_already_gone_clears_the_recorded_rollover_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 5, item 1: ``stop_all_channels``' "already
    gone" branch (a channel whose process already exited but hasn't been
    reaped by ``_poll_process`` yet) is another off-air route that bypasses
    ``_stop`` entirely -- it must clear the entry itself too."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess(pid=111)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        ffmpeg_starter=lambda args: process,
    )
    daemon.process_once("gov")  # start -> ON_AIR, tracked in self._processes

    # command_id=None: unscoped -- this off-air branch's pop is unconditional.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    process.returncode = 0  # exited already, but _poll_process hasn't reaped it yet

    result = daemon.stop_all_channels(deadline_seconds=0.0)

    assert [outcome.outcome for outcome in result.outcomes] == ["already_gone"]
    assert daemon._rollover_plan_end_at == {}


def test_deferred_crash_relaunch_clears_the_recorded_rollover_plan_end(tmp_path: Path) -> None:
    """Coordinator review, round 6, item 1: a crash that recurs within the
    back-off cooldown takes ``_relaunch_after_crash``'s DEFERRED branch --
    the channel sits in ``STARTING`` with no process running for the whole
    cooldown. Unlike the IMMEDIATE relaunch (which re-resolves and starts a
    fresh plan right away, same as the pending-reload restart), this branch
    left the channel dark for a real span of wall-clock time without ever
    clearing the entry -- MEASURED to survive the deferred relaunch (which
    re-resolves its own fresh plan and never reads this dict) and reach a
    LATER, unrelated plain reload once the channel was back on the air.
    This test FAILS if the pop inside the deferred branch is reverted."""
    started: list[_FakeProcess] = []
    clock = _FakeMonotonic(0.0)
    processes = [
        _FakeProcess(pid=111, returncode=None),
        _FakeProcess(pid=222, returncode=None),
        _FakeProcess(pid=333, returncode=None),
    ]
    strategy = _FakeContentReloadStrategy(processes, started)
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        encoder_strategy=strategy,
        restart_cooldown_seconds=15.0,
        monotonic=clock.now,
    )

    daemon.process_once("gov")  # initial start -> ON_AIR (pid 111)

    started[0].returncode = 1
    clock.t = 0.0
    daemon.process_once("gov")  # crash 1 at t=0 -> immediate relaunch (pid 222)
    assert store.read_state("gov").state == "ON_AIR"

    # A rollover plan_end recorded while the channel was briefly back on air --
    # the same shape a stalled automation pass could leave sitting here right
    # before the second crash lands. command_id=None: unscoped -- the
    # deferred branch's pop is unconditional, so no id needs to match.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)

    started[1].returncode = 1
    clock.t = 5.0
    daemon.process_once("gov")  # crash 2 at t=5 (<15) -> DEFER, no new process
    state = store.read_state("gov")
    assert state.state == "STARTING"
    assert daemon._processes.get("gov") is None
    # Must not survive into (or across) the back-off window itself.
    assert daemon._rollover_plan_end_at == {}

    clock.t = 20.0
    daemon.process_once("gov")  # t=20 >= 15 -> deferred relaunch fires (pid 333)
    assert store.read_state("gov").state == "ON_AIR"
    assert daemon._rollover_plan_end_at == {}

    # A genuinely later, unrelated plain reload (automation records a fresh,
    # FUTURE plan_end for its own real rollover -- not the stale past one
    # from before the crash) must defer normally, proving nothing leaked
    # through the back-off window to corrupt it. Scoped to the reload's own
    # id -- a future horizon defers regardless, but scoping it properly is
    # what a real caller does.
    daemon.record_rollover_plan_end(
        "gov", datetime(2099, 1, 1, tzinfo=UTC), command_id="cmd-post-backoff-reload"
    )
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 15, 0, tzinfo=UTC),
            issued_by="automation",
            command_id="cmd-post-backoff-reload",
        )
    )
    daemon.process_once("gov")
    assert strategy.switch_at_end_of_current_calls == [True]


def test_operator_start_superseding_a_deferred_crash_relaunch_does_not_leak_rollover_plan_end(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 6, item 1 (second leak door): an operator
    explicit ``start`` command can supersede a still-deferred crash-relaunch
    before the back-off latch ever permits it to fire on its own --
    ``_process_command`` already pops ``_backoff_relaunch`` for exactly this
    reason (an ERROR from the operator's own start must not be silently
    resurrected later by the automatic relaunch it was meant to replace),
    but was not popping ``_rollover_plan_end_at``. Fixed by the same pop
    inside ``_relaunch_after_crash``'s deferred branch (it fires the moment
    the channel enters the back-off, regardless of how the back-off is later
    resolved) -- this test FAILS if that pop is reverted."""
    started: list[_FakeProcess] = []
    clock = _FakeMonotonic(0.0)
    processes = [
        _FakeProcess(pid=111, returncode=None),
        _FakeProcess(pid=222, returncode=None),
        _FakeProcess(pid=333, returncode=None),
    ]
    strategy = _FakeContentReloadStrategy(processes, started)
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        encoder_strategy=strategy,
        restart_cooldown_seconds=100.0,  # long enough the operator beats it
        monotonic=clock.now,
    )

    daemon.process_once("gov")  # initial start -> ON_AIR (pid 111)
    started[0].returncode = 1
    daemon.process_once("gov")  # crash 1 -> immediate relaunch (pid 222)
    assert store.read_state("gov").state == "ON_AIR"

    # command_id=None: unscoped -- the deferred branch's pop is
    # unconditional, so no id needs to match.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)

    started[1].returncode = 1
    daemon.process_once("gov")  # crash 2 within cooldown -> DEFER (_backoff_relaunch armed)
    assert store.read_state("gov").state == "STARTING"
    assert "gov" in daemon._backoff_relaunch
    assert daemon._rollover_plan_end_at == {}  # popped the moment the defer landed

    # The operator doesn't wait for the latch -- an explicit start supersedes
    # the still-deferred relaunch outright.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-operator-supersede",
        )
    )
    daemon.process_once("gov")  # pops _backoff_relaunch, starts fresh (pid 333)
    assert "gov" not in daemon._backoff_relaunch
    assert store.read_state("gov").state == "ON_AIR"
    assert daemon._rollover_plan_end_at == {}

    # A later, unrelated plain reload (no automation override at all) must
    # defer normally -- proving the earlier crash-window record never
    # survived through to here.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 14, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-plain-reload-after-supersede",
        )
    )
    daemon.process_once("gov")
    assert strategy.switch_at_end_of_current_calls == [True]


def test_start_terminal_error_clears_the_recorded_rollover_plan_end(tmp_path: Path) -> None:
    """Coordinator review, round 6, item 2: ``_start``'s own two terminal-
    ``ERROR`` ``except`` clauses (``ConfigInvalidError``/``SecretUnresolvedError``/
    ``FfmpegNotFoundError``, and the general ``EgressError`` fallback) never
    popped ``_rollover_plan_end_at`` -- ``ERROR`` is off-air by the same
    definition every other route in this file's docstring uses, so a value
    recorded going into a ``_start`` call that lands in ``ERROR`` survived
    across it. This test FAILS if either pop is reverted."""
    store = InMemoryEgressStore()
    # Deliberately no config upserted yet -- ``_start`` raises
    # ConfigInvalidError ("No egress configuration exists for this
    # channel."), landing in the first terminal-ERROR except clause.
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111, returncode=None), _FakeProcess(pid=222, returncode=None)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        encoder_strategy=strategy,
    )

    # command_id=None: unscoped -- ``_start``'s terminal-ERROR pop is
    # unconditional, so no id needs to match.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    daemon.process_once("gov")  # _start raises ConfigInvalidError -> ERROR

    assert store.read_state("gov").state == "ERROR"
    assert daemon._rollover_plan_end_at == {}

    # Fix the config on the SAME daemon instance, start for real, then issue
    # a plain reload with no automation override -- it must defer normally,
    # proving the earlier ERROR-path record never survived to corrupt it.
    store.upsert_config(_config())
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-start-after-error",
        )
    )
    daemon.process_once("gov")
    assert store.read_state("gov").state == "ON_AIR"

    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 14, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-plain-reload-after-error",
        )
    )
    daemon.process_once("gov")
    assert strategy.switch_at_end_of_current_calls == [True]


def test_unscoped_record_never_matches_a_real_queued_reload_and_needs_an_off_air_pop(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 4, item 4 (superseded shape) / round 8: round
    4 measured that an UNSCOPED record (``command_id`` left at its old
    ``None`` "wildcard, matches whatever drains next" default) bound to
    whichever "reload" command for the channel ``_request_reload`` processed
    NEXT -- not necessarily the one automation dispatched it for -- because
    an operator reload issued EARLIER than automation's own drained first
    and wrongly consumed the value.

    Round 8 (coordinator review): that wildcard is gone. ``command_id`` is
    now REQUIRED on ``record_rollover_plan_end`` (round 8, item 3) and
    ``None`` is no longer special-cased -- it matches only a drain whose own
    ``command_id`` is also ``None`` (a direct call bypassing the command
    queue entirely, e.g. ``supervisor.py``). A real ``EgressCommand`` drawn
    from the durable queue always carries a real string id, so an
    unscoped (``command_id=None``) record can now NEVER be consumed by a
    queue-drained reload at all -- every such reload mismatches and leaves
    it in place (round 8's own fix: a mismatch no longer discards, see
    ``_request_reload``'s docstring), and it persists until an off-air route
    (``_stop``, a clean/terminal worker exit, a drain, ...) pops it
    unconditionally, exactly as if the automation caller had never scoped it
    at all. This is the new, safe fate of an unscoped record: inert against
    every reload it wasn't recorded for, cleared only when the channel
    genuinely goes off-air -- never wrongly consumed by whichever reload
    happens to drain first."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # An unscoped record (already stale) -- explicitly ``command_id=None``,
    # never matching any real queued command's id.
    daemon.record_rollover_plan_end("gov", datetime(2020, 1, 1, tzinfo=UTC), command_id=None)
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 11, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-operator-reload",
        )
    )
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
            issued_by="channel-automation",
            command_id="cmd-automation-reload",
        )
    )
    current_label = "Mayor interview"
    daemon.process_once("gov")

    # Both mismatch (neither command_id is None) -- both defer normally, and
    # the unscoped entry is still sitting there, untouched, for lack of a
    # match; it did NOT wrongly bind to whichever reload drained first.
    assert strategy.switch_at_end_of_current_calls == [True, True]
    assert daemon._rollover_plan_end_at == {
        "gov": (None, datetime(2020, 1, 1, tzinfo=UTC), False, None)
    }

    # Only a genuine off-air transition clears it -- an operator stop, here.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="stop",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-operator-stop",
        )
    )
    daemon.process_once("gov")
    assert daemon._rollover_plan_end_at == {}


def test_command_id_scoping_closes_the_immediate_crash_relaunch_leak(tmp_path: Path) -> None:
    """Coordinator review, round 7, item 1 -- the IMMEDIATE crash-relaunch
    path (``_relaunch_after_crash`` -> ``_begin_relaunch`` -> ``_start``)
    deliberately does not pop ``_rollover_plan_end_at`` (round 4: it keeps
    the channel effectively ON_AIR through the transition, relying on
    ``_request_reload``'s own pop -- see that dict's docstring). MEASURED
    without command-id scoping: ``record_rollover_plan_end`` for a rollover
    reload about to be enqueued, then a crash lands and the channel
    relaunches immediately (a fresh process, the recorded ``plan_end_at``
    untouched in the dict) BEFORE that rollover reload command ever drains
    -- and a wholly UNRELATED operator reload landing afterward inherited
    the stale, already-past ``plan_end_at`` and was wrongly cut immediately
    (``switch_at_end_of_current=False``) instead of deferring (``True``,
    the ordinary ON_AIR/no-override shape an unrecorded reload takes).

    Scoping the recorded value to the ``command_id`` it was recorded for
    closes this: the unrelated reload's own command_id does not match.

    Round 8 (coordinator review): a mismatch no longer discards the value
    outright (round 7's ``_request_reload`` popped unconditionally on every
    drain) -- it now LEAVES the entry in place, since the rollover's own
    command never actually drains in this scenario at all (the crash
    preempted it). The entry survives the mismatched reload above, deferring
    it correctly, and is only cleared by a subsequent genuine off-air
    transition (a stop, here) -- proving the leak this test names is closed
    without the round-7 shape of silently discarding a value some OTHER,
    still-pending command might yet have matched."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR (process 111)

    # Automation records a rollover plan_end (already stale) scoped to the
    # specific reload command it is about to enqueue -- but that command is
    # never enqueued here: the crash below preempts it entirely, exactly
    # the scenario this test measures.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="auto-reload-scoped"
    )
    started[0].returncode = 1  # the worker crashes
    daemon.process_once("gov")  # _poll_process relaunches immediately (process 222)

    assert len(started) == 2  # the immediate relaunch really did happen
    # Untouched by the relaunch -- _start must never pop this (round 4).
    assert daemon._rollover_plan_end_at == {
        "gov": ("auto-reload-scoped", datetime(2020, 1, 1, tzinfo=UTC), False, None)
    }

    # A wholly unrelated operator reload, drained afterward -- mismatches
    # ("cmd-reload" != "auto-reload-scoped") and so LEAVES the entry in
    # place (round 8), rather than discarding it as round 7 did.
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))  # command_id="cmd-reload" -- does NOT match
    daemon.process_once("gov")

    assert strategy.reload_calls == ["Mayor interview"]
    assert strategy.switch_at_end_of_current_calls == [True]  # deferred, not wrongly cut
    assert daemon._rollover_plan_end_at == {
        "gov": ("auto-reload-scoped", datetime(2020, 1, 1, tzinfo=UTC), False, None)
    }

    # The rollover's own command_id never actually arrives (dropped, or
    # superseded -- exactly the case this dict's docstring calls out): only
    # a genuine off-air transition clears the leftover entry.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="stop",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-operator-stop",
        )
    )
    daemon.process_once("gov")
    assert daemon._rollover_plan_end_at == {}


def test_command_id_scoping_defers_and_retains_value_when_an_unrelated_reload_drains_first(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 7, item 1 (second shape reviewer asked for)
    -- REWRITTEN, round 8, item 2: round 7 asserted that an unrelated reload
    draining BEFORE the reload the value was actually recorded for
    discarded it outright (the pop was unconditional on every drain,
    matched or not) -- so the automation reload, draining second, found
    nothing left and fell back to deferring. That claim was itself the bug
    round 8 fixes: discarding on a mismatch throws away a value some OTHER,
    still-pending command was genuinely recorded for and hasn't drained yet
    (MEASURED as the 45s issued-timeout retry collision -- see
    ``_rollover_plan_end_at``'s docstring).

    Fixed shape: the unrelated (first-draining) reload mismatches and
    LEAVES the value in place instead of discarding it -- it defers
    normally (nothing recorded FOR it). Automation's own reload, draining
    SECOND, still finds its value waiting and matches -- cutting
    immediately on the already-past horizon it was actually recorded
    against, instead of silently deferring on a boundary that will never
    arrive."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Automation records a rollover plan_end (already stale) scoped to its
    # own about-to-be-enqueued reload command.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="cmd-automation-reload"
    )
    # An unrelated operator reload, issued_at EARLIER, drains first.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 11, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-operator-reload",
        )
    )
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
            issued_by="channel-automation",
            command_id="cmd-automation-reload",
        )
    )
    current_label = "Mayor interview"
    daemon.process_once("gov")

    # The mismatched (operator) reload defers normally -- and, unlike round
    # 7, LEAVES the value in place. The matching (automation) reload, drained
    # in the SAME tick right after, still finds it and cuts on the past
    # horizon it was actually recorded against.
    assert strategy.switch_at_end_of_current_calls == [True, False]
    assert daemon._rollover_plan_end_at == {}


def test_retry_collision_a_stalled_retry_that_overwrites_the_recorded_value_still_cuts(
    tmp_path: Path,
) -> None:
    """Coordinator review, round 8, item 1 -- the actual production trigger
    for the leak this round fixes: ``ChannelAutomationService``'s own 45s
    issued-timeout retry (``_check_plan_rollover``'s ``retrying_undelivered``
    branch). Dispatch A records ``command_id=A`` and enqueues reload A; the
    daemon stalls past the retry timeout; the retry re-records
    (OVERWRITING the dict entry -- ``record_rollover_plan_end`` always
    replaces, never merges) ``command_id=B`` and enqueues reload B. Both A
    and B are real, live commands sitting in the queue when the daemon
    finally catches up -- ``pop_pending_commands`` drains by ``issued_at``,
    so A (issued first) drains before B.

    Round 6 measured the pre-scoping shape as ``[False, True]`` (A wrongly
    cut on a value it was never recorded for). Round 7's scoping fixed
    THAT mixup but popped unconditionally, so A's mismatch discarded B's
    entry before B ever got a chance to drain -- measured ``[True, True]``
    (both defer -- dead air on a horizon already past, never cut at all).
    Round 8's fix (pop only on match) gives the correct shape: A mismatches
    and leaves B's entry in place (defers, no value for A), B then matches
    and cuts on its own already-past horizon."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")  # initial start -> ON_AIR

    # Dispatch A: recorded and enqueued first.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="auto-reload-A"
    )
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 11, 0, tzinfo=UTC),
            issued_by="channel-automation",
            command_id="auto-reload-A",
        )
    )
    # The 45s issued-timeout retry: re-records (OVERWRITING A's entry) and
    # enqueues a SECOND, distinct reload command for the same channel.
    daemon.record_rollover_plan_end(
        "gov", datetime(2020, 1, 1, tzinfo=UTC), command_id="auto-reload-B"
    )
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 11, 0, 45, tzinfo=UTC),
            issued_by="channel-automation",
            command_id="auto-reload-B",
        )
    )
    assert daemon._rollover_plan_end_at == {
        "gov": ("auto-reload-B", datetime(2020, 1, 1, tzinfo=UTC), False, None)
    }

    current_label = "Mayor interview"
    daemon.process_once("gov")  # drains A (issued_at earlier) then B, same tick

    # A: mismatch (B's entry, not A's) -- defers, value left in place for B.
    # B: match -- cuts immediately on the past horizon it was recorded for.
    assert strategy.switch_at_end_of_current_calls == [True, False]
    assert daemon._rollover_plan_end_at == {}


def test_content_reload_never_defers_switch_off_of_fallback_slate(tmp_path: Path) -> None:
    """Issue #157: filler must be interrupted the moment a due program is
    ready -- a reload issued from FALLBACK_SLATE must never defer (wait out
    the rest of a slate/filler leg's own "duration").

    F3(b) (U14, owner-authorized 2026-09-24): that immediate cut no longer
    goes through the engine's in-place selector swap at all -- doing so tore
    down the live slate leg under the selector and wedged a commit for 270 s
    (U13). The policy is unchanged (the switch still must not defer); only the
    MECHANISM moved to the daemon's terminate+restart path, which is why no
    reload request reaches the strategy here and the reload reports "not
    armed" so its caller runs the restart. With no preparer configured there
    is nothing to carry, so the restart re-resolves the plan exactly as a
    declined arm always did -- the case _hold_fallback_slate_restart_plan's
    tests cover WITH a preparer, where the prepared plan is reused."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    processes = [_FakeProcess(pid=111)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Due program"),
        encoder_strategy=strategy,
    )
    # Simulate a live encoder already on FALLBACK_SLATE (no process_once start
    # needed -- _try_content_reload only reads state + the tracked process).
    started.append(processes[0])
    daemon._processes["gov"] = processes[0]  # type: ignore[attr-defined]
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="CivicCast slate",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )

    state = store.read_state("gov")
    assert state is not None
    applied = daemon._try_content_reload("gov", state, processes[0], rollover_plan_end_at=None)  # type: ignore[attr-defined]

    assert applied is False
    # No seamless request was sent to the worker, so no switch policy was ever
    # expressed to it -- the immediate cut is now the restart path's business.
    assert strategy.reload_calls == []
    assert strategy.switch_at_end_of_current_calls == []


def test_content_reload_never_defers_switch_during_a_manual_override(tmp_path: Path) -> None:
    """B1/B3: a reload issued while has_manual_override() is True (a live
    takeover or a forced slate is active) must always cut in immediately --
    the whole point of an operator override is "now", not "wait for the
    outgoing leg to end naturally"."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    processes = [_FakeProcess(pid=111)]
    started: list[_FakeProcess] = [processes[0]]
    strategy = _FakeContentReloadStrategy(processes, started)

    class _OverriddenDaemon(EgressDaemon):
        def has_manual_override(self, channel_id: str) -> bool:
            return True

    daemon = _OverriddenDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Live feed"),
        encoder_strategy=strategy,
    )
    daemon._processes["gov"] = processes[0]  # type: ignore[attr-defined]
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Council meeting",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )

    state = store.read_state("gov")
    assert state is not None
    applied = daemon._try_content_reload("gov", state, processes[0], rollover_plan_end_at=None)  # type: ignore[attr-defined]

    assert applied is True
    assert strategy.switch_at_end_of_current_calls == [False]


def test_content_reload_commit_busy_falls_back_to_restart_with_newest_graph(
    tmp_path: Path,
) -> None:
    """An explicitly rejected overlap restarts onto the complete newest graph."""

    class _CommitBusyStrategy(_FakeContentReloadStrategy):
        def last_send_command_failure_reason(self, channel_id: str) -> str | None:
            return "worker acked 'error' (RuntimeError('reload commit already in progress'))"

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _CommitBusyStrategy(processes, started, reload_ok=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    # the seamless path was attempted, then fell through to the restart drain
    assert strategy.reload_calls == ["Mayor interview"]
    assert store.read_state("gov").state == "TRANSITIONING"

    started[0].returncode = 0  # the drained encoder exits → pending reload restarts
    daemon.process_once("gov")

    assert len(started) == 2  # restart happened
    state = store.read_state("gov")
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"


def test_content_reload_declined_logs_the_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Coordinator follow-up (2026-09-06): a declined seamless reload
    (``reload_content`` returns False) used to fall back to restart with NO log
    line at all -- an operator/on-call reading the control-plane log for "why
    did this channel restart instead of reloading in place" found nothing.
    ``_try_content_reload`` now logs a WARNING naming the channel and, when the
    strategy can report one (``last_send_command_failure_reason``, an OPTIONAL
    capability probed via ``getattr`` like ``supports_content_reload``), why."""

    class _ReasonReportingStrategy(_FakeContentReloadStrategy):
        def last_send_command_failure_reason(self, channel_id: str) -> str | None:
            return "worker acked 'aborted:timeout'"

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _ReasonReportingStrategy(processes, started, reload_ok=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.daemon"):
        daemon.process_once("gov")

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "gov" in message and "declined" in message and "worker acked 'aborted:timeout'" in message
        for message in warnings
    ), warnings


def test_content_reload_declined_with_no_reason_reported_still_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A strategy with no ``last_send_command_failure_reason`` capability at all
    (the plain ``_FakeContentReloadStrategy``, matching every existing strategy
    test double in this file) still gets a WARNING -- just without a reason."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, reload_ok=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.daemon"):
        daemon.process_once("gov")

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gov" in message and "declined" in message for message in warnings), warnings


def test_content_reload_ack_timeout_on_a_live_pid_terminates_the_wedged_worker(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Item 85 daemon-side fix (sandbox soaks 12/14/15): a reload ack timeout
    while the worker's own pid is still alive is the daemon-visible symptom of
    the engine-side reload-commit wedge item 85's engine.py fix addresses -- the
    worker's GLib main-loop thread is blocked forever inside a synchronous
    GStreamer call and will NEVER again answer a pipe command. Before this fix,
    ``_fall_back_to_restart_reload`` only terminated the process for
    ``FALLBACK_SLATE``; for an ordinary ON_AIR channel (this test's case) it left
    the wedged pid running forever, and ``_poll_process`` re-wrote TRANSITIONING
    on every tick because it only ever turns a ``_pending_reloads`` entry into a
    real restart once the worker's OWN exit is observed -- which, for a wedged
    pid, never happens on its own. This proves the fix: the wedged process is
    terminated immediately (bounded terminate -> wait -> kill; this fake process
    reports exited the instant ``terminate()`` is called, so no kill escalation
    is exercised here), and the very next poll tick observes the exit and
    restarts the channel cleanly instead of pinning TRANSITIONING forever."""

    class _AckTimeoutStrategy(_FakeContentReloadStrategy):
        def last_send_command_failure_reason(self, channel_id: str) -> str | None:
            return "ack timeout after 5.0s (reissue_desired_state)"

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    wedged_process = _FakeProcess(pid=111)
    fresh_process = _FakeProcess(pid=222)
    processes = [wedged_process, fresh_process]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _AckTimeoutStrategy(processes, started, reload_ok=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")  # starts wedged_process, writes ON_AIR
    assert not wedged_process.terminated

    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.daemon"):
        daemon.process_once("gov")  # the declined reload -- must terminate the wedged worker

    assert wedged_process.terminated, "a reload ack timeout on a live pid must terminate it"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gov" in message and "wedged" in message for message in warnings), warnings

    # The fake process reports exited (returncode=0) the instant terminate() is
    # called, so the NEXT poll tick's ordinary worker-exit handling restarts the
    # channel -- proving the pending-reload entry does not pin TRANSITIONING
    # against a pid that (in this fixed behavior) is no longer running.
    daemon.process_once("gov")
    assert started == [wedged_process, fresh_process]
    state = store.read_state("gov")
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"


def test_content_reload_ack_timeout_escalates_to_kill_when_terminate_is_ignored(
    tmp_path: Path,
) -> None:
    """Hostile-review follow-up: the previous test's fake process reports
    exited the INSTANT ``terminate()`` is called, which never exercises the
    ``kill()`` escalation ``_process_terminate_bounded`` provides for exactly
    this reason -- a worker wedged inside a synchronous GStreamer call may not
    even observe (let alone act on) a graceful terminate request.
    ``_StubbornFakeProcess`` ignores ``terminate()`` entirely (``poll()`` keeps
    reporting "still alive" no matter how many times it's called) and only
    ends on ``kill()``, with a distinct returncode (137) proving THAT call
    path is what actually ended it."""

    class _AckTimeoutStrategy(_FakeContentReloadStrategy):
        def last_send_command_failure_reason(self, channel_id: str) -> str | None:
            return "ack timeout after 5.0s (reissue_desired_state)"

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    wedged_process = _StubbornFakeProcess(pid=111)
    fresh_process = _FakeProcess(pid=222)
    processes = [wedged_process, fresh_process]
    started: list[object] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _AckTimeoutStrategy(processes, started, reload_ok=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")  # starts wedged_process, writes ON_AIR
    assert wedged_process.terminate_calls == 0
    assert not wedged_process.killed

    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # the declined reload -- must escalate to kill()

    assert wedged_process.terminate_calls >= 1, "terminate() must still be tried first"
    assert wedged_process.killed, "a terminate()-ignoring process must be escalated to kill()"
    assert wedged_process.returncode == 137

    # kill() actually ended the process (unlike the ignored terminate()), so the
    # next poll tick's ordinary worker-exit handling restarts the channel.
    daemon.process_once("gov")
    assert started == [wedged_process, fresh_process]
    state = store.read_state("gov")
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"


def test_content_reload_declined_for_a_reason_other_than_ack_timeout_does_not_terminate(
    tmp_path: Path,
) -> None:
    """Narrow scope check: a decline for a reason OTHER than an ack timeout (a
    plan-prepare failure, a synchronous build error, etc.) must NOT terminate an
    otherwise-healthy worker -- only a lost ack on a confirmed-alive pid (the
    specific wedge symptom) escalates to termination. ``_FakeContentReloadStrategy``
    with no ``last_send_command_failure_reason`` capability (matching every other
    existing declined-reload test in this file) models exactly that: a decline
    with no reason at all, therefore never matching the "ack timeout" check."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess(pid=111)
    processes = [process, _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, reload_ok=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert not process.terminated


def test_pending_reload_does_not_terminate_the_worker_while_awaiting_settlement(
    tmp_path: Path,
) -> None:
    """F1 BLOCKER fix (coordinator hostile review): a deferred/boundary-aligned
    switch (an automation-driven ON_AIR extension) can take minutes to settle.
    The pre-redesign code bounded the pipe ack itself at a fixed timeout, so a
    correctly-armed long-lead reload would time out that wait and the daemon
    would terminate a perfectly healthy worker. This redesign never blocks on
    settlement at all -- across many poll ticks with reload-status.json still
    absent, the worker must NEVER be restarted or terminated; only once the
    status file actually appears does the daemon act (and even then, only to
    finish the ON_AIR bookkeeping, not to touch the process)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")  # initial start -> ON_AIR
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # armed; strategy did NOT write reload-status.json

    assert strategy.reload_calls == ["Mayor interview"]
    assert len(started) == 1  # no restart happened to arm it

    # Many more ticks with settlement still pending -- this is standing in for
    # however long a real deferred switch's natural wait runs (up to
    # defer_switch_timeout_s=900s in the engine); the point is that NOTHING
    # here is time-bounded on this side, so no amount of ticks alone forces a
    # restart the way the old synchronous ack-wait design would have.
    for _ in range(20):
        daemon.process_once("gov")

    assert started[0].terminated is False  # never terminated while pending
    assert len(started) == 1  # never restarted
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Council meeting"  # still the OLD label -- honest

    # The reload NOW settles (a real worker's on_settled finally fires).
    _write_fake_reload_status(tmp_path, "gov", strategy.reload_ids[-1], "applied")
    daemon.process_once("gov")
    assert started[0].terminated is False
    assert len(started) == 1
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"


def test_queued_stop_wins_over_pending_reload_after_clean_worker_exit(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        encoder_strategy=strategy,
    )
    daemon.process_once("gov")
    daemon._pending_reloads["gov"] = ("ON_AIR", "Council meeting")  # type: ignore[attr-defined]
    assert daemon._pending_reloads  # type: ignore[attr-defined]
    store.enqueue_command(_command("stop").model_copy(update={"command_id": "cmd-stop"}))
    started[0].returncode = 0
    daemon.process_once("gov")
    assert store.read_state("gov").state == "STOPPED"  # type: ignore[union-attr]
    assert len(started) == 1


def test_pending_reload_settlement_deadline_falls_back_to_restart(tmp_path: Path) -> None:
    """F1 redesign: if a status update never arrives at all (e.g. the worker
    crashed between arming and writing reload-status.json), the daemon must
    not wait forever -- past _PENDING_RELOAD_SETTLE_DEADLINE_S it gives up and
    falls back to the terminate+restart path, same as a synchronously-declined
    reload always did."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    fake_now = [1_000_000.0]
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
        monotonic=lambda: fake_now[0],
    )

    daemon.process_once("gov")  # initial start -> ON_AIR
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # armed; no status file ever written

    assert len(started) == 1  # no restart yet -- still within the deadline

    # Advance the injected clock past the deadline with no status update ever
    # arriving.
    fake_now[0] += 961.0
    daemon.process_once("gov")

    assert store.read_state("gov").state == "TRANSITIONING"  # fell back to restart
    started[0].returncode = 0  # the drained encoder exits -> pending reload restarts
    daemon.process_once("gov")

    assert len(started) == 2  # restart landed the program change
    state = store.read_state("gov")
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"


def test_unrecognized_settlement_result_is_treated_as_aborted_immediately(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Hostile-review follow-up: a settlement result that is neither
    "applied" nor "aborted:<reason>" (a malformed write, a future/older
    worker version) must be treated as aborted IMMEDIATELY -- not silently
    waited out for the full 960s deadline, since whatever wrote it clearly
    ran and waiting longer buys nothing."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    fake_now = [1_000_000.0]
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
        monotonic=lambda: fake_now[0],
    )

    daemon.process_once("gov")  # initial start -> ON_AIR
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # armed; no status file ever written

    armed_reload_id = strategy.reload_ids[-1]
    _write_fake_reload_status(tmp_path, "gov", armed_reload_id, "weird-unknown-value")

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.daemon"):
        # Only ONE second later -- nowhere near the 960s deadline -- proving
        # this is recognized and acted on immediately, not merely eventually.
        fake_now[0] += 1.0
        daemon.process_once("gov")

    assert store.read_state("gov").state == "TRANSITIONING"  # fell back to restart already
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "gov" in message and "unrecognized" in message and armed_reload_id in message
        for message in messages
    ), messages

    started[0].returncode = 0  # the drained encoder exits -> pending reload restarts
    daemon.process_once("gov")
    assert len(started) == 2
    state = store.read_state("gov")
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"


def test_aborted_reload_settlement_is_operator_visible_and_durable(tmp_path: Path) -> None:
    """F-8: a worker-side media/build abort must survive the restart transition.

    The state row gives the operator the immediate reason, while the proof event
    remains available after a rapid successful restart replaces that live row.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")
    _write_fake_reload_status(
        tmp_path,
        "gov",
        strategy.reload_ids[-1],
        "aborted:new program errored before commit",
    )

    daemon.process_once("gov")

    transitioning = store.read_state("gov")
    assert transitioning is not None
    assert transitioning.state == "TRANSITIONING"
    assert transitioning.last_error == (
        "Seamless content reload failed: aborted:new program errored before commit"
    )
    failure_event = store.recent_proof_events("gov", 1)[0]
    assert failure_event.event_id == transitioning.current_proof_event_id
    assert failure_event.source_path == "content-reload:settlement-aborted"
    assert "new program errored before commit" in failure_event.machine_summary

    started[0].returncode = 0
    daemon.process_once("gov")

    assert store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]
    durable_events = store.recent_proof_events("gov", 20)
    assert any(event.event_id == failure_event.event_id for event in durable_events)


def test_worker_crash_during_the_armed_window_discards_pending_settlement(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Hostile-review follow-up, item 1: a worker that crashes WHILE a reload
    it armed is still settling must not leave that tracking behind -- the
    entry is gone immediately (no spurious restart fires 960s later against a
    channel that has already been relaunched onto something else), and a
    LATE-arriving "applied" status for that dead attempt is recognized and
    ignored (logged), not silently discarded with no evidence it was even
    observed."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222), _FakeProcess(pid=333)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    fake_now = [1_000_000.0]
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
        monotonic=lambda: fake_now[0],
    )

    daemon.process_once("gov")  # initial start -> ON_AIR (pid 111)
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # armed; strategy did NOT write reload-status.json

    armed_reload_id = strategy.reload_ids[-1]
    assert daemon._pending_reload_settle.get("gov") is not None  # type: ignore[attr-defined]

    # The worker crashes (non-zero exit) WHILE the reload is still armed --
    # nothing ever settled it.
    started[0].returncode = 1
    daemon.process_once("gov")  # _poll_process observes the crash -> relaunches at once

    assert daemon._pending_reload_settle.get("gov") is None  # type: ignore[attr-defined]
    assert len(started) == 2  # the crash relaunch landed (pid 222)

    # Well past the 960s settlement deadline the ARMED reload itself would
    # have been allowed to take (hostile-review follow-up, third pass, P2:
    # _discarded_reload_ids is keyed by channel_id -- already bounded by the
    # number of channels this daemon tracks -- and carries no expiry of its
    # own; a real settlement for a dead attempt can legitimately land any
    # time after the crash, including well past that budget, and must still
    # be recognized), the dead attempt's worker finally writes its status
    # file. Recognized and ignored, logged; no additional restart triggered.
    fake_now[0] += 1000.0
    with caplog.at_level(logging.INFO, logger="civiccast.egress.daemon"):
        _write_fake_reload_status(tmp_path, "gov", armed_reload_id, "applied")
        daemon.process_once("gov")

    assert len(started) == 2  # the late write changes nothing
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "gov" in message and armed_reload_id in message and "ignoring" in message
        for message in messages
    ), messages
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"


def _prepare_with_tracked_plan_dirs(
    tmp_path: Path, counter: dict[str, int]
) -> Callable[[EgressSourcePlan, EgressConfig], SourcePreparationReport]:
    """A fake ``source_preparer`` that mints a fresh, real, uniquely-named
    directory (mirroring ``SourcePreparer.prepare``'s own per-call directory)
    on every call, so tests can assert on exactly which one gets released."""

    def prepare(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        counter["n"] += 1
        plan_dir = tmp_path / f"plan-{counter['n']}"
        plan_dir.mkdir()
        return SourcePreparationReport(source_plan=source_plan, records=(), plan_dir=plan_dir)

    return prepare


def test_async_start_expiring_during_preparation_releases_once_then_prepares_fresh_slate(
    tmp_path: Path,
) -> None:
    """A short scheduled item that expires before launch is never spawned.

    The completed async operation must be removed before the one bounded
    fallback operation is queued, and only the fallback's freshly prepared
    directory may become active.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    clock = [0.0]
    provider_calls = 0
    prepared_labels: list[str] = []
    released: list[Path] = []
    started: list[_FakeProcess] = []
    started_labels: list[str] = []
    counter = {"n": 0}
    base_prepare = _prepare_with_tracked_plan_dirs(tmp_path, counter)
    initial_prepare_entered = Event()
    release_initial_prepare = Event()

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        nonlocal provider_calls
        provider_calls += 1
        clock[0] += 20.0
        plan = _source_plan(tmp_path)
        segment = plan.segments[0].model_copy(update={"duration_seconds": 30.0})
        return plan.model_copy(update={"segments": [segment]})

    def prepare(plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepared_labels.append(plan.segments[0].label)
        if plan.segments[0].label == "Council meeting":
            initial_prepare_entered.set()
            assert release_initial_prepare.wait(timeout=10.0)
            clock[0] += 10.0
        return base_prepare(plan, config)

    class _CapturingStrategy(_FakeContentReloadStrategy):
        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            started_labels.append(request.source_plan.segments[0].label)
            return super().start(request)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        boundary_source_plan_provider=lambda _channel_id, _at: pytest.fail(
            "startup expiry must not chase the schedule"
        ),
        encoder_strategy=_CapturingStrategy([_FakeProcess(pid=111)], started),
        source_preparer=prepare,
        prepared_plan_release=released.append,
        monotonic=lambda: clock[0],
    )
    daemon.enable_async_preparation()
    try:
        daemon.process_once("gov")
        # Hold the first preparation across this tick's end-of-pass poll. A
        # fast worker may otherwise advance to fallback before this returns.
        assert initial_prepare_entered.wait(timeout=10.0)
        release_initial_prepare.set()
        daemon._preparations["gov"].future.result(timeout=2)  # type: ignore[attr-defined]

        daemon.process_once("gov")
        assert set(daemon._preparations) == {"gov"}  # type: ignore[attr-defined]
        assert released == [tmp_path / "plan-1"]
        assert started_labels == []

        daemon._preparations["gov"].future.result(timeout=2)  # type: ignore[attr-defined]
        daemon.process_once("gov")

        assert provider_calls == 1
        assert prepared_labels == ["Council meeting", "Fallback slate"]
        assert started_labels == ["Fallback slate"]
        assert released == [tmp_path / "plan-1"]
        assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-2"})
        assert store.read_state("gov").state == "FALLBACK_SLATE"  # type: ignore[union-attr]
    finally:
        release_initial_prepare.set()
        daemon.shutdown_preparation()


def test_unexpired_scheduled_start_still_launches_the_prepared_program(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    clock = [0.0]
    fallback_calls = 0
    started: list[_FakeProcess] = []
    started_labels: list[str] = []
    counter = {"n": 0}
    base_prepare = _prepare_with_tracked_plan_dirs(tmp_path, counter)

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        clock[0] += 20.0
        plan = _source_plan(tmp_path)
        segment = plan.segments[0].model_copy(update={"duration_seconds": 30.0})
        return plan.model_copy(update={"segments": [segment]})

    def fallback_provider(_config: EgressConfig) -> EgressSourcePlan:
        nonlocal fallback_calls
        fallback_calls += 1
        return _slate_plan(tmp_path)

    def prepare(plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        clock[0] += 9.0
        return base_prepare(plan, config)

    class _CapturingStrategy(_FakeContentReloadStrategy):
        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            started_labels.append(request.source_plan.segments[0].label)
            return super().start(request)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=fallback_provider,
        boundary_source_plan_provider=lambda _channel_id, _at: pytest.fail("not called"),
        encoder_strategy=_CapturingStrategy([_FakeProcess(pid=111)], started),
        source_preparer=prepare,
        monotonic=lambda: clock[0],
    )

    daemon.process_once("gov")

    assert fallback_calls == 0
    assert started_labels == ["Council meeting"]
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})
    assert store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]


def test_superseding_a_pending_reload_releases_its_previous_plan_dir(tmp_path: Path) -> None:
    """Hostile-review follow-up, item 3: ``_try_content_reload`` used to
    silently overwrite ``_pending_reload_settle`` when a newer reload attempt
    superseded a still-pending one, leaking the replaced entry's plan_dir
    forever (never released, never GC'd until age/budget eventually caught
    it)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"
    counter = {"n": 0}
    released: list[Path] = []

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
    )

    daemon.process_once("gov")  # initial start -> plan-1 (becomes ACTIVE, not pending)
    assert released == []

    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # arms reload #1 -> plan-2 (PENDING, never settles)
    assert released == []

    current_label = "Evening news"
    # A distinct command_id -- ``_command("reload")`` always returns the SAME
    # fixed id ("cmd-reload"), which the store's dedup would silently drop as
    # a re-enqueue of the already-consumed first reload command.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime(2026, 6, 5, 12, 1, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-reload-2",
        )
    )
    daemon.process_once("gov")  # a second rollover supersedes reload #1

    # The FIRST reload's plan (plan-2) is released the moment it is
    # superseded -- not left dangling until GC eventually notices.
    assert released == [tmp_path / "plan-2"]


def test_stop_releases_both_the_pending_reload_and_the_active_plan_dir(tmp_path: Path) -> None:
    """Hostile-review follow-up (third pass): a direct (non-draining) operator
    stop must route through BOTH shared discard helpers -- an armed-but-
    unsettled reload's plan_dir (``_discard_pending_reload_settlement``) AND
    the channel's currently-active plan_dir (``_discard_active_prepared_plan_
    dir``) -- rather than either being left for GC. The direct stop path's
    own ``_process_terminate`` call makes the worker's exit synchronous with
    this call, so releasing the active dir here (unlike the draining path,
    see the next test) is safe."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"
    counter = {"n": 0}
    released: list[Path] = []

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
    )

    daemon.process_once("gov")  # initial start -> plan-1, tracked ACTIVE
    assert daemon._active_prepared_plan_dir.get("gov") == tmp_path / "plan-1"  # type: ignore[attr-defined]

    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # arms a reload -> plan-2, tracked PENDING (never settles)
    assert daemon._pending_reload_settle.get("gov") is not None  # type: ignore[attr-defined]
    assert released == []

    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")

    assert daemon._pending_reload_settle.get("gov") is None  # type: ignore[attr-defined]
    assert daemon._active_prepared_plan_dir.get("gov") is None  # type: ignore[attr-defined]
    # _discard_pending_reload_settlement runs before _discard_active_prepared_
    # plan_dir inside _stop, so the pending reload's plan (plan-2) is
    # released first, then the active plan (plan-1).
    assert released == [tmp_path / "plan-2", tmp_path / "plan-1"]
    assert store.read_state("gov").state == "STOPPED"


def test_draining_stop_defers_the_active_plan_dir_release_to_stop_all_channels(
    tmp_path: Path,
) -> None:
    """Hostile-review follow-up (third pass), P2: a DRAINING stop
    (``stop_all_channels``'s graceful path) only sends the worker its
    TERMINAL command and returns -- the worker may still be airing from the
    active plan directory for the entire drain deadline, so ``_stop`` itself
    must NOT release it. Proves the directory survives the draining ``_stop``
    call untouched, and is only released once ``stop_all_channels`` actually
    observes the worker's exit."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess(pid=111)
    processes = [process]
    started: list[_FakeProcess] = []
    counter = {"n": 0}
    released: list[Path] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        encoder_strategy=_FakeContentReloadStrategy(processes, started),
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
    )

    daemon.process_once("gov")  # _start -> plan-1, tracked ACTIVE
    assert daemon._active_prepared_plan_dir.get("gov") == tmp_path / "plan-1"  # type: ignore[attr-defined]

    result = daemon.stop_all_channels(deadline_seconds=0.0)  # worker never exits in time
    assert result.outcomes[0].outcome == "killed_after_deadline"

    # The worker was force-terminated by the deadline escalation -- ITS exit
    # is what unblocks the release, which this same stop_all_channels call
    # then performs once that escalation confirms it.
    assert released == [tmp_path / "plan-1"]
    assert daemon._active_prepared_plan_dir.get("gov") is None  # type: ignore[attr-defined]


def test_worker_exit_between_poll_process_and_poll_reload_settlement_falls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Hostile-review follow-up (third pass): the liveness re-check inside
    ``_poll_reload_settlement`` (guarding the "applied" -> ``_commit_reload_
    settlement`` transition) must catch a worker that was still alive when
    ``_poll_process`` checked it EARLIER in this same ``process_once`` tick,
    but has exited by the time this later check runs -- proving the
    liveness check is a live re-read, not reused/stale information from
    earlier in the same pass. Falls back to restart, but (since the crash
    is only discovered mid-tick, after the healthy-poll bookkeeping already
    ran) the actual relaunch does not fire until the FOLLOWING tick, once
    ``_poll_process`` itself observes the same exit."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    class _ExitsOnSecondPollProcess(_FakeProcess):
        """Alive on its FIRST ``poll()`` call, exited (non-zero) on every
        call after that -- models a worker dying in the gap between two
        poll checks within the SAME process_once tick."""

        def __init__(self, *, pid: int, alive_for_calls: int) -> None:
            super().__init__(pid=pid, returncode=None)
            self._poll_calls = 0
            self._alive_for_calls = alive_for_calls

        def poll(self) -> int | None:
            self._poll_calls += 1
            if self._poll_calls <= self._alive_for_calls:
                return None
            self.returncode = 1
            return 1

    # Alive for its first 3 poll() calls (the reload-arming tick's own
    # _poll_process check, that same tick's _request_reload liveness check
    # gating whether it even attempts the seamless path, and the critical
    # tick's OWN _poll_process check) -- exited from the 4th call onward,
    # which is _poll_reload_settlement's liveness re-check later in that
    # SAME critical tick.
    worker = _ExitsOnSecondPollProcess(pid=111, alive_for_calls=3)
    restart_process = _FakeProcess(pid=222)
    processes = [worker, restart_process]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(processes, started, auto_settle=False)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")  # initial start -> ON_AIR (pid 111, poll call #1 pending)
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # armed; no status file ever written

    armed_reload_id = strategy.reload_ids[-1]
    _write_fake_reload_status(tmp_path, "gov", armed_reload_id, "applied")

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.daemon"):
        # ONE tick: _poll_process runs first and calls worker.poll() (call #1
        # -> None, still alive) -- no crash-relaunch fires from THAT check.
        # _poll_reload_settlement runs later in this SAME tick, sees the
        # "applied" status, and does its OWN liveness re-check (call #2 ->
        # 1, exited) -- falls back to restart, but does not itself relaunch.
        daemon.process_once("gov")

    assert daemon._pending_reload_settle.get("gov") is None  # type: ignore[attr-defined]
    assert len(started) == 1  # no relaunch yet -- this tick only fell back
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "TRANSITIONING"
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "gov" in message and "already exited" in message and armed_reload_id in message
        for message in messages
    ), messages

    # The FOLLOWING tick: _poll_process now observes the same exit (call #3,
    # still 1) as a fresh crash and relaunches.
    daemon.process_once("gov")
    assert len(started) == 2
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Mayor interview"


def test_start_tracks_and_releases_its_active_plan_dir_on_worker_exit(tmp_path: Path) -> None:
    """Hostile-review follow-up, item 4: the fake strategy here
    (``_FakeContentReloadStrategy``) reports ``supports_content_reload=True``
    (the beta.5 default), but this test never enqueues a reload command, so
    the reload path (``_try_content_reload``/``_commit_reload_settlement``)
    never runs regardless of that flag -- exactly as for a channel opted out
    via ``CIVICCAST_EGRESS_SEAMLESS_RELOAD=0``. ``_start`` is the only thing
    that ever prepares a plan for this channel; without this the ONLY cleanup
    for its directory was age/budget GC. Proves ``_start`` tracks its own
    plan as active and releases it once the worker exits."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111)]
    started: list[_FakeProcess] = []
    counter = {"n": 0}
    released: list[Path] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        encoder_strategy=_FakeContentReloadStrategy(processes, started),
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
    )

    daemon.process_once("gov")  # _start -> plan-1
    process = started[0]
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})
    assert released == []

    store.enqueue_command(_command("stop").model_copy(update={"command_id": "cmd-stop-on-exit"}))
    process.returncode = 0  # a clean exit (e.g. an operator-issued stop landed)
    daemon.process_once("gov")

    assert released == [tmp_path / "plan-1"]
    assert daemon.live_prepared_plan_dirs("gov") == frozenset()


def test_content_reload_strategy_exception_falls_back_to_restart(tmp_path: Path) -> None:
    """TEST-004 #5: if reload_content RAISES, the daemon logs and falls back to
    terminate+restart rather than letting a strategy bug kill the program change."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _FakeContentReloadStrategy(
        processes, started, reload_exc=RuntimeError("worker FIFO exploded")
    )
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == ["Mayor interview"]  # the seamless path was attempted
    assert store.read_state("gov").state == "TRANSITIONING"  # then fell through to restart
    started[0].returncode = 0
    daemon.process_once("gov")
    assert len(started) == 2  # restart landed the program change


def test_content_reload_foreign_channel_plan_falls_back_to_restart(tmp_path: Path) -> None:
    """TEST-004 #3: a plan whose channel_id doesn't match is not applied seamlessly —
    the daemon falls through to the restart path (it never calls reload_content)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    foreign = False

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        plan = _source_plan_with_label(
            tmp_path, "Mayor interview" if foreign else "Council meeting"
        )
        if foreign:
            object.__setattr__(plan, "channel_id", "someone-else")
        return plan

    strategy = _FakeContentReloadStrategy(processes, started)
    daemon = EgressDaemon(
        store, work_dir=tmp_path, source_plan_provider=source_provider, encoder_strategy=strategy
    )
    daemon.process_once("gov")
    foreign = True
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert strategy.reload_calls == []  # foreign plan rejected before reload_content
    assert store.read_state("gov").state == "TRANSITIONING"  # fell through to restart


class _CapturingEncoderStrategy:
    """Captures every EncoderStartRequest passed to start()/reload_content() so a
    test can assert what actually reached the encoder -- used here to pin the
    S15 cg_overlay_provider pass-through at BOTH EncoderStartRequest call sites
    in daemon.py (the ``_start`` start path and the ``_try_content_reload``
    seamless-swap path)."""

    name = "fake-capturing-strategy"
    supports_live_swap = False
    supports_content_reload = True

    def __init__(self, processes: list[_FakeProcess]) -> None:
        self._processes = processes
        self.start_requests: list[EncoderStartRequest] = []
        self.reload_requests: list[EncoderStartRequest] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        self.start_requests.append(request)
        process = self._processes.pop(0)
        return EncoderStartResult(
            process=process,
            concat_plan_path=request.work_dir / "playout-graph.json",
            stdout_path=request.work_dir / "out.log",
            stderr_path=request.work_dir / "err.log",
            args=("worker",),
        )

    def swap_role(self, channel_id: str, work_dir: Path, role: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def reload_content(
        self, channel_id: str, work_dir: Path, request: EncoderStartRequest, **_kwargs: Any
    ) -> bool:
        self.reload_requests.append(request)
        return True


def test_cg_overlay_provider_result_reaches_encoder_start_request_on_start(tmp_path: Path) -> None:
    """S15 CG-lite engine overlay leg: the daemon's cg_overlay_provider hook is
    supposed to hand its per-channel board raster to the encoder strategy via
    EncoderStartRequest.cg_overlay_image -- the start-path call site inside
    EgressDaemon._start. Had no direct coverage."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    overlay_path = tmp_path / "board-overlay" / "gov.png"
    overlay_calls: list[tuple[str, EgressConfig]] = []

    def cg_overlay_provider(channel_id: str, config: EgressConfig) -> Path | None:
        overlay_calls.append((channel_id, config))
        return overlay_path

    strategy = _CapturingEncoderStrategy([_FakeProcess(pid=111)])
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        encoder_strategy=strategy,
        cg_overlay_provider=cg_overlay_provider,
    )

    daemon.process_once("gov")

    assert [channel_id for channel_id, _config in overlay_calls] == ["gov"]
    assert len(strategy.start_requests) == 1
    assert strategy.start_requests[0].cg_overlay_image == overlay_path


def test_cg_overlay_provider_result_reaches_encoder_start_request_on_reload(tmp_path: Path) -> None:
    """Same pass-through pinned at the second EncoderStartRequest call site
    (EgressDaemon._try_content_reload) -- a seamless in-place program swap must
    keep the board overlay wired, not drop it because no encoder restart
    happened."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    overlay_path = tmp_path / "board-overlay" / "gov.png"
    current_label = "Council meeting"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return _source_plan_with_label(tmp_path, current_label)

    strategy = _CapturingEncoderStrategy([_FakeProcess(pid=111), _FakeProcess(pid=222)])
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        encoder_strategy=strategy,
        cg_overlay_provider=lambda _channel_id, _config: overlay_path,
    )

    daemon.process_once("gov")
    current_label = "Mayor interview"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert len(strategy.start_requests) == 1, "the reload must stay seamless, not restart"
    assert len(strategy.reload_requests) == 1
    assert strategy.reload_requests[0].cg_overlay_image == overlay_path


def test_proof_event_redacts_live_source_credentials(tmp_path: Path) -> None:
    """ENG-003: a live segment's ingest URI (which can carry an SRT passphrase) must be
    redacted before it lands in the durable proof chain."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        return EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(
                    label="Live: chamber",
                    path="srt://truck.example:7001?passphrase=hunter2",
                    duration_seconds=1,
                    kind="live",
                )
            ],
        )

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        ffmpeg_starter=lambda _args: process,
    )
    daemon.process_once("gov")

    proof = store.recent_proof_events("gov", 1)[0]
    assert "hunter2" not in proof.source_path
    assert (
        "passphrase=%3Credacted%3E" in proof.source_path
        or "passphrase=<redacted>" in proof.source_path
    )


def _orphan_state_row(pid: int) -> EgressStateRow:
    return EgressStateRow(
        channel_id="gov",
        state="ON_AIR",
        current_source_label="Council meeting",
        updated_at=datetime(2026, 6, 12, 7, 0, tzinfo=UTC),
        pid=pid,
    )


def _old_ffmpeg(pid: int):  # type: ignore[no-untyped-def]
    """Probe stub: pid is an ffmpeg created long before this server booted."""
    from civiccast.egress.daemon import OrphanInfo

    return lambda probed: OrphanInfo(name="ffmpeg.exe", created_at=0.0) if probed == pid else None


class TestOrphanEncoderReap:
    """Issue #161 (CA-8 live finding): a server restart leaves the previous
    server's encoder children streaming to the sink ports; the new daemon
    must reap them before starting its own encoder, or two writers corrupt
    the stream. Audit ENG-001/TEST-004 hardening: reap only PRE-BOOT pids,
    never pids this daemon tracks, and the terminator re-verifies identity
    by create time (closes the pid-reuse TOCTOU)."""

    def test_startup_reaps_orphaned_ffmpeg_before_starting(self, tmp_path: Path) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config())
        # The previous server process left this state row behind; pid 7777
        # is still running, is an ffmpeg image, and predates this daemon.
        store.write_state(_orphan_state_row(7777))
        store.enqueue_command(_command())
        process = _FakeProcess(pid=8888)
        terminated: list[tuple[int, float]] = []

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
            ffmpeg_starter=lambda _args: process,
            orphan_probe=_old_ffmpeg(7777),
            orphan_terminator=lambda pid, created_at: terminated.append((pid, created_at)),
        )

        daemon.process_once("gov")

        assert terminated == [(7777, 0.0)]
        state = store.read_state("gov")
        assert state is not None
        assert state.state == "ON_AIR"
        assert state.pid == 8888
        reap_events = [
            e for e in store.recent_proof_events("gov", 5) if "orphan" in e.machine_summary
        ]
        assert len(reap_events) == 1

    def test_reused_pid_belonging_to_another_program_is_never_touched(self, tmp_path: Path) -> None:
        from civiccast.egress.daemon import OrphanInfo

        store = InMemoryEgressStore()
        store.upsert_config(_config())
        store.write_state(_orphan_state_row(7777))
        store.enqueue_command(_command())
        terminated: list[tuple[int, float]] = []

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
            ffmpeg_starter=lambda _args: _FakeProcess(pid=8888),
            orphan_probe=lambda _pid: OrphanInfo(name="notepad.exe", created_at=0.0),
            orphan_terminator=lambda pid, created_at: terminated.append((pid, created_at)),
        )

        daemon.process_once("gov")

        assert terminated == []
        assert store.read_state("gov").pid == 8888  # type: ignore[union-attr]

    def test_ffmpeg_created_after_this_server_booted_is_never_reaped(self, tmp_path: Path) -> None:
        # Audit ENG-001: a freed pid recycled onto a FRESH ffmpeg (another
        # channel's encoder, a conform job, a relay) must never be killed -
        # only processes that predate this daemon can be its predecessor's.
        import time as _time

        from civiccast.egress.daemon import OrphanInfo

        store = InMemoryEgressStore()
        store.upsert_config(_config())
        store.write_state(_orphan_state_row(7777))
        store.enqueue_command(_command())
        terminated: list[tuple[int, float]] = []

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
            ffmpeg_starter=lambda _args: _FakeProcess(pid=8888),
            orphan_probe=lambda _pid: OrphanInfo(name="ffmpeg.exe", created_at=_time.time() + 3600),
            orphan_terminator=lambda pid, created_at: terminated.append((pid, created_at)),
        )

        daemon.process_once("gov")

        assert terminated == []
        assert store.read_state("gov").pid == 8888  # type: ignore[union-attr]

    def test_pid_tracked_by_this_daemon_is_never_probed_or_reaped(self, tmp_path: Path) -> None:
        # Audit ENG-001: a pid this daemon already tracks belongs to it.
        store = InMemoryEgressStore()
        store.upsert_config(_config())
        store.write_state(_orphan_state_row(4242))
        store.enqueue_command(_command())
        probed: list[int] = []

        def failing_probe(pid: int):  # type: ignore[no-untyped-def]
            probed.append(pid)
            return

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
            ffmpeg_starter=lambda _args: _FakeProcess(pid=4242),
            orphan_probe=failing_probe,
            orphan_terminator=lambda pid, created_at: pytest.fail("must not terminate"),
        )

        daemon.process_once("gov")  # starts; tracks pid 4242
        daemon.process_once("gov")  # live tracked process: no probe at all

        assert 4242 not in probed[1:]  # never probed once tracked

    def test_dead_state_pid_needs_no_reap(self, tmp_path: Path) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config())
        store.write_state(_orphan_state_row(7777))
        store.enqueue_command(_command())
        terminated: list[tuple[int, float]] = []

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
            ffmpeg_starter=lambda _args: _FakeProcess(pid=8888),
            orphan_probe=lambda _pid: None,
            orphan_terminator=lambda pid, created_at: terminated.append((pid, created_at)),
        )

        daemon.process_once("gov")

        assert terminated == []
        assert store.read_state("gov").pid == 8888  # type: ignore[union-attr]

    def test_live_tracked_process_is_not_treated_as_an_orphan(self, tmp_path: Path) -> None:
        # A normally-running channel (this daemon owns the encoder) must not
        # reap its own process on subsequent passes.
        store = InMemoryEgressStore()
        store.upsert_config(_config())
        store.enqueue_command(_command())
        terminated: list[tuple[int, float]] = []

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
            ffmpeg_starter=lambda _args: _FakeProcess(pid=8888),
            orphan_probe=_old_ffmpeg(8888),
            orphan_terminator=lambda pid, created_at: terminated.append((pid, created_at)),
        )

        daemon.process_once("gov")
        daemon.process_once("gov")

        assert terminated == []

    def test_pending_reload_clears_the_state_pid_before_restarting(self, tmp_path: Path) -> None:
        # Audit ENG-001/TEST-004: the reload takeover path used to leave the
        # just-killed encoder's pid in the state row, so _reap_orphan probed
        # a FREED pid on every filler->program takeover (and unit tests
        # probed the host's real process table). The pid must be cleared
        # before the pending-reload _start, so the probe never sees it.
        store = InMemoryEgressStore()
        store.upsert_config(_config())
        store.enqueue_command(_command())
        source_available = False
        processes = [_KilledNonZeroProcess(pid=111), _FakeProcess(pid=222)]
        started: list[_FakeProcess] = []
        probed: list[int] = []

        def source_provider(_channel_id: str) -> EgressSourcePlan | None:
            return _source_plan(tmp_path) if source_available else None

        def recording_probe(pid: int):  # type: ignore[no-untyped-def]
            probed.append(pid)
            return

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=source_provider,
            fallback_source_provider=lambda _config: _slate_plan(tmp_path),
            ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
            orphan_probe=recording_probe,
            orphan_terminator=lambda pid, created_at: pytest.fail("must not terminate"),
        )

        daemon.process_once("gov")
        source_available = True
        store.enqueue_command(_command("reload"))
        daemon.process_once("gov")  # kills filler (pid 111), pending reload
        daemon.process_once("gov")  # exit observed -> pending reload starts

        assert store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]
        # The freed pid 111 was never offered to the orphan probe.
        assert 111 not in probed


def test_stop_discards_a_pending_reload_kill_flag(tmp_path: Path) -> None:
    """Audit ENG-005/TEST-009: a reload-on-slate kill flag that leaks past a
    stop can later misclassify a genuine crash as a clean reload handoff.
    Sequence: slate reload (flag set, encoder still dying) -> stop -> start ->
    graceful ON_AIR reload pending -> CRASH. The crash must surface the
    honest relaunch error, not silently flow into the pending reload."""

    class _SlowDyingProcess(_FakeProcess):
        def terminate(self) -> None:  # stays alive until the test says so
            self.terminated = True

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    source_available = False
    slow = _SlowDyingProcess(pid=111)
    processes: list[_FakeProcess] = [slow, _FakeProcess(pid=222), _FakeProcess(pid=333)]
    started: list[_FakeProcess] = []
    current_label = ["Council meeting"]

    def source_provider(_channel_id: str) -> EgressSourcePlan | None:
        return _source_plan_with_label(tmp_path, current_label[0]) if source_available else None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        orphan_probe=lambda _pid: None,
        orphan_terminator=lambda pid, created_at: None,
    )

    daemon.process_once("gov")  # slate (fallback)
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")  # kill flag set; slow process still alive
    store.enqueue_command(_command("stop").model_copy(update={"command_id": "cmd-stop-1"}))
    daemon.process_once("gov")  # stop: must ALSO discard the kill flag
    slow.returncode = 0

    source_available = True
    store.enqueue_command(_command().model_copy(update={"command_id": "cmd-start-2"}))
    daemon.process_once("gov")  # ON_AIR on pid 222
    current_label[0] = "Mayor interview"
    store.enqueue_command(_command("reload").model_copy(update={"command_id": "cmd-reload-2"}))
    daemon.process_once("gov")  # graceful drain pending (no kill)

    started[1].returncode = 1  # CRASH, not a clean drain
    daemon.process_once("gov")

    # The honest crash bookkeeping ran: a child-failure event exists (the
    # stale-flag bug routed the crash into the pending reload, skipping it).
    events = store.recent_proof_events("gov", 10)
    assert any(e.event_id.startswith("egress-encoder-child-relaunch-") for e in events)


def test_default_orphan_seams_probe_and_terminate_a_real_child() -> None:
    """Audit TEST-004: the production psutil pair was never executed by any
    test. Pin it against a real spawned child, including the create-time
    identity check that closes the pid-reuse TOCTOU."""

    import subprocess
    import sys

    from civiccast.egress.daemon import (
        _default_orphan_probe,
        _default_orphan_terminator,
    )

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        info = _default_orphan_probe(child.pid)
        assert info is not None
        assert "python" in info.name.lower()

        # Wrong create_time = identity mismatch: must NOT kill.
        _default_orphan_terminator(child.pid, info.created_at + 9999.0)
        assert child.poll() is None

        # Matching identity: terminates.
        _default_orphan_terminator(child.pid, info.created_at)
        child.wait(timeout=15)
    finally:
        if child.poll() is None:
            child.kill()


class _KilledNonZeroProcess(_FakeProcess):
    """Real ffmpeg exits non-zero when terminated; the stock fake exits 0."""

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1


def test_reload_interrupts_filler_instead_of_draining_the_fill_target(
    tmp_path: Path,
) -> None:
    """Issue #157 (CA-8 live finding): after #154, filler plans span an hour,
    so a drain-style reload delays a due program by up to that hour. A reload
    issued while the channel is on FALLBACK_SLATE filler must terminate the
    filler encoder and start the program when it exits - even though a real
    terminated ffmpeg exits non-zero - without the crash-relaunch error."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    source_available = False
    processes: list[_FakeProcess] = [
        _KilledNonZeroProcess(pid=111),
        _FakeProcess(pid=222),
    ]
    started: list[_FakeProcess] = []

    def source_provider(_channel_id: str) -> EgressSourcePlan | None:
        return _source_plan(tmp_path) if source_available else None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    daemon.process_once("gov")
    assert store.read_state("gov").state == "FALLBACK_SLATE"  # type: ignore[union-attr]

    source_available = True
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    # The filler encoder is killed NOW, not drained to the end of its plan.
    assert started[0].terminated is True

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Council meeting"
    # The deliberate kill must not masquerade as an encoder crash.
    assert state.last_error is None
    proof_events = store.recent_proof_events("gov", 2)
    assert proof_events[0].state == "ON_AIR"
    assert "exited fallback slate" in proof_events[0].machine_summary


def test_reload_still_drains_programs_gracefully(tmp_path: Path) -> None:
    """Issue #157 boundary: ON_AIR programming is never cut for a reload."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    daemon.process_once("gov")
    assert store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]

    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")

    assert started[0].terminated is False
    assert store.read_state("gov").state == "TRANSITIONING"  # type: ignore[union-attr]


def test_daemon_records_live_takeover_and_handback_on_reload(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222), _FakeProcess(pid=333)]
    started: list[_FakeProcess] = []
    current_label = "Council meeting"
    current_kind = "program"

    def source_provider(_channel_id: str) -> EgressSourcePlan:
        source = tmp_path / f"{current_label.replace(' ', '-').lower()}.ts"
        source.write_text(current_label, encoding="utf-8")
        return EgressSourcePlan(
            channel_id="gov",
            segments=[
                EgressSourceSegment(
                    label=current_label,
                    path=str(source),
                    duration_seconds=1,
                    kind=current_kind,  # type: ignore[arg-type]
                )
            ],
        )

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    daemon.process_once("gov")
    current_label = "Live: Council chamber"
    current_kind = "live"
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")
    started[0].returncode = 0
    daemon.process_once("gov")
    current_label = "Council meeting"
    current_kind = "program"
    store.enqueue_command(
        _command("reload").model_copy(update={"command_id": "cmd-reload-handback"})
    )
    daemon.process_once("gov")
    started[1].returncode = 0
    daemon.process_once("gov")

    proof_events = store.recent_proof_events("gov", 5)
    assert [event.state for event in proof_events] == [
        "ON_AIR",
        "TRANSITIONING",
        "ON_AIR",
        "TRANSITIONING",
        "ON_AIR",
    ]
    assert "released live source" in proof_events[0].machine_summary
    assert "began an egress handoff" in proof_events[1].machine_summary
    assert "put live source" in proof_events[2].machine_summary
    assert "began live takeover" in proof_events[3].machine_summary


def test_daemon_relaunches_running_encoder_when_child_exits_nonzero(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111, returncode=None), _FakeProcess(pid=222, returncode=None)]
    started: list[_FakeProcess] = []
    alert_calls: list[tuple[str, str, float | None, float | None]] = []
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        alert_evaluator_hook=lambda channel, state, fps, bitrate: alert_calls.append(
            (channel, state, fps, bitrate)
        ),
    )

    daemon.process_once("gov")
    started[0].returncode = 1
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.pid == 222
    assert len(started) == 2
    assert processes == []
    assert store.recent_health("gov", 2)[1].state == "STARTING"
    error_calls = [call for call in alert_calls if call[1] == "ERROR"]
    assert error_calls == [("gov", "ERROR", None, None)]
    proof_events = store.recent_proof_events("gov", 4)
    assert [event.state for event in proof_events] == [
        "ON_AIR",
        "TRANSITIONING",
        "STARTING",
        "ON_AIR",
    ]
    assert "non-zero FFmpeg child exit" in proof_events[2].machine_summary
    assert "started encoder relaunch" in proof_events[2].machine_summary


@pytest.mark.parametrize("alert_fails", [False, True])
def test_daemon_relaunches_running_encoder_when_child_exits_cleanly_on_air(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    alert_fails: bool,
) -> None:
    """A worker's rc=0 is still a fault while the channel is supposed to air.

    Regression for F-1(b): the old clean-exit branch wrote STOPPED, making an
    unexpected worker death indistinguishable from an operator stop.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111, returncode=None), _FakeProcess(pid=222, returncode=None)]
    started: list[_FakeProcess] = []
    fault_alerts: list[str] = []

    def alert_hook(channel: str, state: str, fps: float | None, bitrate: float | None) -> None:
        if state == "ERROR":
            fault_alerts.append(channel)
            if alert_fails:
                raise RuntimeError("alert delivery unavailable")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        alert_evaluator_hook=alert_hook,
    )

    daemon.process_once("gov")
    started[0].returncode = 0
    with caplog.at_level("INFO", logger="civiccast.egress.daemon"):
        daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.pid == 222
    assert len(started) == 2
    assert fault_alerts == ["gov"]
    assert store.recent_health("gov", 2)[1].state == "STARTING"
    failure = store.recent_proof_events("gov", 4)[2]
    assert failure.state == "STARTING"
    assert "exited cleanly while the channel was expected to stay on air" in failure.machine_summary
    assert "worker exited (exit_code=0, state=ON_AIR, desired=ACTIVE" in caplog.text
    assert "Worker exited cleanly while the channel was expected to stay on air" in caplog.text


def test_daemon_relaunches_clean_exit_while_transitioning(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )
    daemon.process_once("gov")
    row = store.read_state("gov")
    assert row is not None
    store.write_state(row.model_copy(update={"state": "TRANSITIONING"}))
    started[0].returncode = 0
    daemon.process_once("gov")
    assert store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]
    assert len(started) == 2


def test_repeated_clean_exits_use_existing_backoff_and_retain_fault(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222), _FakeProcess(pid=333)]
    started: list[_FakeProcess] = []
    clock = [100.0]
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        monotonic=lambda: clock[0],
        restart_cooldown_seconds=15.0,
    )
    daemon.process_once("gov")
    started[0].returncode = 0
    daemon.process_once("gov")  # immediate relaunch
    started[1].returncode = 0
    daemon.process_once("gov")  # second exit is inside the cooldown
    row = store.read_state("gov")
    assert row is not None
    assert row.state == "STARTING"
    assert "cleanly repeatedly" in (row.last_error or "")
    assert len(started) == 2


@pytest.mark.parametrize("action", ["stop", "drain"])
@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("pending_restart", [False, True])
def test_queued_operator_stop_or_drain_wins_over_clean_worker_exit(
    tmp_path: Path, action: str, exit_code: int, pending_restart: bool
) -> None:
    """A deliberate command queued before polling suppresses clean-exit recovery."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111, returncode=None)]
    started: list[_FakeProcess] = []
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    daemon.process_once("gov")
    store.enqueue_command(_command(action).model_copy(update={"command_id": f"cmd-{action}"}))
    if pending_restart:
        daemon._pending_reloads["gov"] = ("ON_AIR", "Mayor interview")
        daemon._reload_kills.add("gov")
    started[0].returncode = exit_code
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    # Polling reaps the exited worker before the queued command is drained;
    # the command still wins by preventing an automatic relaunch, and then
    # observes the already-gone worker as a normal stopped channel.
    assert state.state == "STOPPED"
    assert len(started) == 1


def test_daemon_records_error_for_unresolved_secret(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config(secret_ref="EGRESS_SRT_PASSPHRASE"))
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        resolve_secret=lambda _ref: None,
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ERROR"
    assert "not resolved" in (state.last_error or "")


def test_daemon_rejects_source_plan_for_wrong_channel(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    source_plan = _source_plan(tmp_path).model_copy(update={"channel_id": "schools"})
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: source_plan,
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ERROR"
    assert "does not match" in (state.last_error or "")


class _FakeReconnectStrategy:
    """A GstPlayoutStrategy stand-in exposing the optional D2 worker-pipe seam
    (send_command / reconnect_channel / close_channel) so the daemon's CC-WS5-006
    reconnect + close wiring can be proven without gi or a real named pipe."""

    name = "fake-gst-reconnect"
    supports_live_swap = True
    supports_content_reload = True

    def __init__(self, processes: list[_FakeProcess], started: list[_FakeProcess]) -> None:
        self._processes = processes
        self._started = started
        self.reconnect_calls: list[str] = []
        self.close_calls: list[str] = []
        self.stop_sends: list[str] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        process = self._processes.pop(0)
        self._started.append(process)
        return EncoderStartResult(
            process=process,
            concat_plan_path=request.work_dir / "playout-graph.json",
            stdout_path=request.work_dir / "out.log",
            stderr_path=request.work_dir / "err.log",
            args=("worker",),
        )

    def swap_role(self, channel_id: str, work_dir: Path, role: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def reload_content(
        self, channel_id: str, work_dir: Path, request: EncoderStartRequest, **_kwargs: Any
    ) -> bool:  # pragma: no cover
        return True

    def send_command(self, work_dir: Path, channel_id: str, text: str) -> bool:
        self.stop_sends.append(text)
        return True

    def reconnect_channel(self, channel_id: str) -> list[str]:
        self.reconnect_calls.append(channel_id)
        return []

    def close_channel(self, channel_id: str) -> None:
        self.close_calls.append(channel_id)


def test_crash_relaunch_replays_worker_channel_desired_state(tmp_path: Path) -> None:
    """CC-WS5-006 defect 3 (reconnect wiring): when the daemon crash-relaunches a
    channel's worker, it must call the strategy's reconnect_channel so the desired
    state (reload/swap) is replayed over the fresh worker pipe."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    strategy = _FakeReconnectStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")
    assert strategy.reconnect_calls == []  # a fresh start is NOT a reconnect
    started[0].returncode = 1  # worker crashes
    daemon.process_once("gov")

    assert store.read_state("gov").state == "ON_AIR"  # relaunched
    assert len(started) == 2
    assert strategy.reconnect_calls == ["gov"]  # reconnect replay was wired in


def test_operator_stop_closes_worker_pipe_channel(tmp_path: Path) -> None:
    """CC-WS5-006 defect 3 (close wiring): an operator stop must close the channel's
    worker pipe so the named-pipe server is not leaked."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111)]
    started: list[_FakeProcess] = []
    strategy = _FakeReconnectStrategy(processes, started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        encoder_strategy=strategy,
    )

    daemon.process_once("gov")
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")

    assert store.read_state("gov").state == "STOPPED"
    assert strategy.close_calls == ["gov"]


def test_stop_all_channels_closes_worker_pipe_channels(tmp_path: Path) -> None:
    """CC-WS5-006 defect 3 (shutdown close wiring): a graceful drain-all closes each
    channel's worker pipe once its process has exited."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111, returncode=None)]  # alive at drain time
    started: list[_FakeProcess] = []
    strategy = _FakeReconnectStrategy(processes, started)

    def fake_sleep(_seconds: float) -> None:
        started[0].returncode = 0  # the worker exits while the drain loop waits

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        encoder_strategy=strategy,
        sleep=fake_sleep,
    )

    daemon.process_once("gov")
    result = daemon.stop_all_channels(deadline_seconds=5.0)

    assert strategy.stop_sends == ["stop"]  # graceful terminal command was sent
    assert strategy.close_calls == ["gov"]  # pipe closed on shutdown
    assert [o.outcome for o in result.outcomes] == ["drained"]


def test_daemon_stop_command_terminates_running_encoder(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "STOPPED"
    assert process.terminated is True


def test_daemon_drain_command_waits_for_encoder_to_finish(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")
    store.enqueue_command(_command("drain"))
    daemon.process_once("gov")

    draining_state = store.read_state("gov")
    assert draining_state is not None
    assert draining_state.state == "DRAINING"
    assert process.terminated is False
    assert store.recent_health("gov", 1)[0].state == "DRAINING"

    process.returncode = 0
    daemon.process_once("gov")

    stopped_state = store.read_state("gov")
    assert stopped_state is not None
    assert stopped_state.state == "STOPPED"


def test_daemon_reads_encoder_metrics_from_ffmpeg_stderr_log(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")
    log_path = tmp_path / "gov" / "logs" / "ffmpeg.stderr.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("frame= 300 fps=29.97 bitrate=6200.5kbits/s drop=2\n", encoding="utf-8")
    daemon.process_once("gov")

    health = store.recent_health("gov", 1)[0]
    assert health.encoder_fps == 29.97
    assert health.encoder_bitrate_kbps == 6200.5
    assert health.dropped_frames == 2


def test_daemon_uses_sink_health_provider_for_running_encoder(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    seen: dict[str, object] = {}

    def sink_health_provider(channel_id, config, metrics):  # type: ignore[no-untyped-def]
        seen["channel_id"] = channel_id
        seen["sink_labels"] = [sink.label for sink in config.sinks]
        seen["fps"] = metrics.encoder_fps
        return {"Proof": False}

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        sink_health_provider=sink_health_provider,
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")
    log_path = tmp_path / "gov" / "logs" / "ffmpeg.stderr.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("frame= 300 fps=29.97 bitrate=6200.5kbits/s drop=2\n", encoding="utf-8")
    daemon.process_once("gov")

    health = store.recent_health("gov", 1)[0]
    assert health.state == "ON_AIR"
    assert health.sink_connected == {"Proof": False}
    assert seen == {"channel_id": "gov", "sink_labels": ["Proof"], "fps": 29.97}


def test_daemon_default_sink_health_does_not_assume_srt_connected(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config(secret_ref="EGRESS_SRT_PASSPHRASE"))
    store.enqueue_command(_command())
    process = _FakeProcess()

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        resolve_secret=lambda _ref: "station-passphrase",
        ffmpeg_starter=lambda _args: process,
    )

    daemon.process_once("gov")

    health = store.recent_health("gov", 1)[0]
    assert health.sink_connected == {"Headend": False}


def test_playout_supervisor_combines_lookahead_plans_for_one_encoder_start(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess(pid=101)
    lookahead_calls: list[tuple[str, int]] = []
    upstream_calls: list[str] = []
    started_labels: list[list[str]] = []
    plans = [
        _source_plan_with_label(tmp_path, "Council meeting"),
        _source_plan_with_label(tmp_path, "Mayor interview"),
    ]

    def lookahead_provider(channel_id: str, window: int) -> list[EgressSourcePlan]:
        lookahead_calls.append((channel_id, window))
        return plans

    def start_encoder(request: EncoderStartRequest) -> EncoderStartResult:
        started_labels.append([segment.label for segment in request.source_plan.segments])
        stderr_path = tmp_path / "gov" / "logs" / "ffmpeg.stderr.log"
        return EncoderStartResult(
            process=process,
            concat_plan_path=tmp_path / "gov" / "egress-source-plan.ffconcat",
            stdout_path=tmp_path / "gov" / "logs" / "ffmpeg.stdout.log",
            stderr_path=stderr_path,
            args=("ffmpeg",),
        )

    supervisor = PlayoutSupervisor(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda channel_id: upstream_calls.append(channel_id) or None,
        lookahead_source_plan_provider=lookahead_provider,
        lookahead_window=2,
        encoder_strategy=type(
            "Strategy",
            (),
            {"name": "test", "start": staticmethod(start_encoder)},
        )(),
    )

    supervisor.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Council meeting"
    assert state.pid == 101
    assert started_labels == [["Council meeting", "Mayor interview"]]
    assert lookahead_calls == [("gov", 2)]
    assert upstream_calls == []


def test_playout_supervisor_falls_back_to_single_source_provider(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    source_calls: list[str] = []

    supervisor = PlayoutSupervisor(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda channel_id: (
            source_calls.append(channel_id) or _source_plan_with_label(tmp_path, "Council meeting")
        ),
        ffmpeg_starter=lambda _args: process,
    )

    supervisor.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Council meeting"
    assert source_calls == ["gov"]


def test_playout_supervisor_live_takeover_and_handback_use_explicit_live_plan(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [
        _FakeProcess(pid=101),
        _FakeProcess(pid=202),
        _FakeProcess(pid=303),
    ]
    started: list[_FakeProcess] = []
    schedule_labels = ["Council meeting", "Mayor interview"]

    def lookahead_provider(channel_id: str, window: int) -> list[EgressSourcePlan]:
        return [
            _source_plan_with_label(tmp_path, schedule_labels.pop(0))
            for _index in range(min(window, len(schedule_labels)))
        ]

    supervisor = PlayoutSupervisor(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        lookahead_source_plan_provider=lookahead_provider,
        lookahead_window=1,
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    supervisor.process_once("gov")
    supervisor.request_live_takeover(channel_id="gov", live_source_plan=_live_source_plan(tmp_path))
    takeover_state = store.read_state("gov")
    assert takeover_state is not None
    assert takeover_state.state == "TRANSITIONING"

    started[0].returncode = 0
    supervisor.process_once("gov")
    live_state = store.read_state("gov")
    assert live_state is not None
    assert live_state.state == "ON_AIR"
    assert live_state.current_source_label == "Live: Council chamber"

    supervisor.request_live_handback(channel_id="gov")
    handback_state = store.read_state("gov")
    assert handback_state is not None
    assert handback_state.state == "TRANSITIONING"

    started[1].returncode = 0
    supervisor.process_once("gov")
    scheduled_state = store.read_state("gov")
    assert scheduled_state is not None
    assert scheduled_state.state == "ON_AIR"
    assert scheduled_state.current_source_label == "Mayor interview"
    assert [process.pid for process in started] == [101, 202, 303]
    summaries = [event.machine_summary for event in store.recent_proof_events("gov", 5)]
    assert any("began live takeover" in summary for summary in summaries)
    assert any("released live source" in summary for summary in summaries)


@pytest.mark.parametrize("program_due", [False, True])
@pytest.mark.parametrize(
    ("content_reload", "reload_refused"), [(False, False), (True, False), (True, True)]
)
def test_live_handback_cuts_open_feed_to_due_program_or_slate(
    tmp_path: Path, program_due: bool, content_reload: bool, reload_refused: bool
) -> None:
    """End must release an open live feed without waiting for its EOS."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    strategy_type = _FakeContentReloadStrategy if content_reload else _FakeNonReloadCapableStrategy
    strategy = strategy_type(processes, started, reload_ok=not reload_refused)
    scheduled = _source_plan_with_label(tmp_path, "Mayor interview")
    slate = _slate_plan(tmp_path)
    supervisor = PlayoutSupervisor(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: scheduled if program_due else None,
        fallback_source_provider=lambda _config: slate,
        encoder_strategy=strategy,
    )
    supervisor._live_takeover_plans["gov"] = _live_source_plan(tmp_path)
    supervisor.process_once("gov")
    live_process = started[0]

    supervisor.request_live_handback(channel_id="gov")
    if content_reload and not reload_refused:
        assert strategy.reload_calls == [
            scheduled.segments[0].label if program_due else slate.segments[0].label
        ]
        assert strategy.switch_at_end_of_current_calls == [False]
        assert not live_process.terminated
    else:
        assert live_process.terminated, "An open live feed cannot finish a graceful drain"
    supervisor.process_once("gov")
    state = store.read_state("gov")
    assert state is not None
    assert state.state == ("ON_AIR" if program_due else "FALLBACK_SLATE")
    assert state.current_source_label == (
        scheduled.segments[0].label if program_due else slate.segments[0].label
    )


def test_quality_recovery_cleanup_only_clears_its_matching_forced_slate(
    tmp_path: Path,
) -> None:
    supervisor = PlayoutSupervisor(
        InMemoryEgressStore(),
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
    )
    supervisor._forced_slate_reasons["gov"] = "automatic quality recovery cooldown"
    supervisor._source_lookahead["gov"] = []

    assert (
        supervisor.clear_fallback_slate(channel_id="gov", reason="a newer operator slate") is False
    )
    assert supervisor.has_manual_override("gov") is True

    assert (
        supervisor.clear_fallback_slate(
            channel_id="gov", reason="automatic quality recovery cooldown"
        )
        is True
    )
    assert supervisor.has_manual_override("gov") is False
    assert "gov" not in supervisor._source_lookahead


def test_playout_supervisor_forces_fallback_slate_and_exits_to_schedule(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [
        _FakeProcess(pid=101),
        _FakeProcess(pid=202),
        _FakeProcess(pid=303),
    ]
    started: list[_FakeProcess] = []
    schedule_labels = ["Council meeting", "Mayor interview"]

    def lookahead_provider(channel_id: str, window: int) -> list[EgressSourcePlan]:
        return [
            _source_plan_with_label(tmp_path, schedule_labels.pop(0))
            for _index in range(min(window, len(schedule_labels)))
        ]

    supervisor = PlayoutSupervisor(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        lookahead_source_plan_provider=lookahead_provider,
        lookahead_window=1,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
    )

    supervisor.process_once("gov")
    supervisor.request_fallback_slate(channel_id="gov", reason="scheduled asset missing")
    slate_transition = store.read_state("gov")
    assert slate_transition is not None
    assert slate_transition.state == "TRANSITIONING"

    started[0].returncode = 0
    supervisor.process_once("gov")
    slate_state = store.read_state("gov")
    assert slate_state is not None
    assert slate_state.state == "FALLBACK_SLATE"
    assert slate_state.current_source_label == "Fallback slate"
    assert slate_state.last_error == "scheduled asset missing"

    supervisor.request_slate_exit(channel_id="gov")
    exit_transition = store.read_state("gov")
    assert exit_transition is not None
    assert exit_transition.state == "TRANSITIONING"

    started[1].returncode = 0
    supervisor.process_once("gov")
    scheduled_state = store.read_state("gov")
    assert scheduled_state is not None
    assert scheduled_state.state == "ON_AIR"
    assert scheduled_state.current_source_label == "Mayor interview"
    assert [process.pid for process in started] == [101, 202, 303]


def test_playout_supervisor_raises_and_clears_cg_overlay_immediately(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    process = _FakeProcess()
    supervisor = PlayoutSupervisor(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: process,
    )
    proof = build_cg_overlay_egress_proof(
        overlay=build_emergency_overlay(overlay_id="notice-1", severity="warning"),
        overlay_contract=build_overlay_contract(channel_id="gov"),
    )

    supervisor.process_once("gov")
    supervisor.raise_cg_emergency_overlay(proof=proof)
    supervisor.clear_cg_emergency_overlay(channel_id="gov")

    overlay_events = [
        event
        for event in store.recent_proof_events("gov", 5)
        if event.proof_boundary == CG_EGRESS_PROOF_BOUNDARY
    ]
    assert [event.source_label for event in overlay_events] == [
        "CivicCast emergency banner cleared",
        "CivicCast emergency banner",
    ]
    assert overlay_events[0].state == "ON_AIR"
    assert "not an EAS claim" in overlay_events[0].machine_summary


# --- S9-5 crash-relaunch back-off -----------------------------------------------------


class _FakeMonotonic:
    """An injectable monotonic clock for deterministic latch cooldown tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t


def _backoff_daemon(
    tmp_path: Path, started: list[_FakeProcess], pids, cooldown: float, monotonic=None
):
    """A daemon whose ffmpeg_starter hands out fake processes with the given pids.
    ``monotonic`` is injected through the public constructor seam so deferral tests
    drive the back-off clock deterministically without touching private state."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=pid, returncode=None) for pid in pids]
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        restart_cooldown_seconds=cooldown,
        monotonic=monotonic,
    )
    return daemon, store


def test_first_crash_relaunches_immediately_then_rapid_repeat_defers(tmp_path: Path) -> None:
    """The first crash relaunches at once (fast one-off recovery); a crash that recurs
    within the cooldown is paced, not hot-looped — no new process this tick."""
    started: list[_FakeProcess] = []
    daemon, store = _backoff_daemon(tmp_path, started, (111, 222, 333), cooldown=100.0)

    daemon.process_once("gov")  # start → pid 111 on air
    started[0].returncode = 1
    daemon.process_once("gov")  # crash 1 → immediate relaunch → pid 222
    assert len(started) == 2
    assert store.read_state("gov").state == "ON_AIR"

    started[1].returncode = 1
    daemon.process_once("gov")  # crash 2 within cooldown → DEFER, no new process
    assert len(started) == 2
    state = store.read_state("gov")
    assert state.state == "STARTING"
    assert "backing off" in (state.last_error or "")
    # the failure was still recorded as a proof event (no silent swallow)
    assert any(
        e.source_path == "ffmpeg-child:nonzero-exit" for e in store.recent_proof_events("gov", 12)
    )


def test_deferred_relaunch_fires_once_cooldown_elapses(tmp_path: Path) -> None:
    """A deferred crash-relaunch lands on the process_once tick after the cooldown."""
    started: list[_FakeProcess] = []
    clock = _FakeMonotonic(0.0)
    daemon, store = _backoff_daemon(
        tmp_path, started, (111, 222, 333), cooldown=15.0, monotonic=clock.now
    )

    daemon.process_once("gov")  # start → 111
    started[0].returncode = 1
    clock.t = 0.0
    daemon.process_once("gov")  # crash 1 at t=0 → relaunch 222 (arms next=15)
    assert len(started) == 2

    started[1].returncode = 1
    clock.t = 5.0
    daemon.process_once("gov")  # crash 2 at t=5 (<15) → defer
    assert len(started) == 2
    assert store.read_state("gov").state == "STARTING"

    clock.t = 20.0
    daemon.process_once("gov")  # t=20 ≥ 15 → deferred relaunch fires → 333
    assert len(started) == 3
    assert store.read_state("gov").state == "ON_AIR"


@pytest.mark.parametrize("exit_code", [0, 1])
def test_restart_escalation_event_fires_at_threshold_and_every_multiple(
    tmp_path: Path, exit_code: int
) -> None:
    """A distinct restart-escalation proof event (the S8 alerting hook seam) fires at the
    escalation streak AND RE-FIRES at every multiple (n, 2n, …) so a long crash loop keeps
    alerting. The in-between streaks emit nothing — this pins the modulo boundary so a
    regression to fire-once cannot pass silently."""
    started: list[_FakeProcess] = []
    n = _RESTART_ESCALATION_STREAK
    pids = tuple(100 + i for i in range(2 * n + 1))  # start + 2n relaunches
    daemon, store = _backoff_daemon(tmp_path, started, pids, cooldown=0.0)  # never defer

    def escalation_count() -> int:
        return sum(
            1
            for e in store.recent_proof_events("gov", 100)
            if e.source_path == "ffmpeg-child:restart-escalation"
        )

    daemon.process_once("gov")  # start (streak 0)
    counts: list[int] = []
    for _ in range(2 * n):
        started[-1].returncode = exit_code
        daemon.process_once("gov")  # cooldown=0 → every crash relaunches; streak climbs 1..2n
        counts.append(escalation_count())

    # counts[i] = escalation count after crash i+1. Fire at streak n and 2n; quiet between.
    assert counts[n - 1] == 1, "escalation did not fire at the first threshold (streak n)"
    assert counts[2 * n - 2] == 1, "an in-between streak (2n-1) wrongly escalated"
    assert counts[2 * n - 1] == 2, "escalation did not RE-FIRE at the second multiple (streak 2n)"

    escalations = [
        e
        for e in store.recent_proof_events("gov", 100)
        if e.source_path == "ffmpeg-child:restart-escalation"
    ]
    assert "crash-relaunched" in escalations[0].machine_summary
    # recent-first: escalations[0] is the streak-2n event, escalations[1] the streak-n event.
    assert str(2 * n) in escalations[0].machine_summary
    assert str(n) in escalations[1].machine_summary


def test_healthy_uptime_resets_the_crash_streak(tmp_path: Path) -> None:
    """A worker that stays up healthily -- REAL observed evidence, held for
    the full healthy-uptime window -- clears the crash streak, so a later
    failure is treated as a fresh one-off (immediate relaunch, no escalation
    carry-over).

    Round-3 fix (PR #183 review, BLOCKER item 1): this reset used to fire on
    wall-clock seconds since spawn alone (this test used to force it by
    back-dating ``daemon._started_at`` directly, with no encoder evidence of
    any kind) -- that let a worker whose SPAWN overhead alone (interpreter
    start, import, graph build, or -- for the GStreamer strategy -- the
    preroll wait itself) crossed the threshold reset the streak despite
    never producing any real output. It now requires observed on-air
    evidence (see ``EgressDaemon._observed_on_air_evidence``): for this
    FFmpeg-strategy channel, real fps/bitrate progress written to the
    worker's own stderr log, held for the healthy-uptime window from the
    moment it was FIRST observed -- never from spawn time."""
    clock = {"t": 0.0}
    started: list[_FakeProcess] = []
    daemon, store = _backoff_daemon(
        tmp_path, started, (100, 101, 102), cooldown=0.0, monotonic=lambda: clock["t"]
    )

    daemon.process_once("gov")  # start → 100
    started[0].returncode = 1
    daemon.process_once("gov")  # crash 1 → streak 1 → relaunch 101
    assert daemon._restart_streak.get("gov") == 1

    # The relaunched worker (101) is now genuinely producing output -- write
    # real ffmpeg progress into ITS stderr log, the evidence this reset now
    # requires (not merely time since spawn).
    log_path = daemon._stderr_logs["gov"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("frame=100 fps=30.0 bitrate=6000.0kbits/s\n", encoding="utf-8")
    clock["t"] += 1.0
    daemon.process_once("gov")  # evidence observed and latched this tick
    assert daemon._restart_streak.get("gov") == 1  # not reset yet -- 0s held so far
    assert "gov" in daemon._on_air_confirmed_at

    # Past the 60s healthy-uptime window since evidence was FIRST observed.
    clock["t"] += 65.0
    daemon.process_once("gov")  # healthy poll → streak cleared
    assert daemon._restart_streak.get("gov") is None
    assert store.read_state("gov").state == "ON_AIR"


class _EncoderUnavailableThenSlateStrategy:
    """First ``start()`` (the program plan) raises ``EncoderUnavailableError``;
    the retry (the slate plan) succeeds. Models degraded-mode tier 4: the egress
    encoder is unavailable for the program, so the daemon must put up the
    technical-difficulties slate rather than dropping the channel to dead air."""

    name = "fake-encoder-unavailable-then-slate"
    supports_live_swap = False
    supports_content_reload = False

    def __init__(self) -> None:
        self.start_labels: list[str] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        from civiccast.egress.errors import EncoderUnavailableError

        label = request.source_plan.segments[0].label
        self.start_labels.append(label)
        if label != "Fallback slate":
            raise EncoderUnavailableError("no usable egress encoder (ffmpeg absent)")
        return EncoderStartResult(
            process=_FakeProcess(pid=515),
            concat_plan_path=request.work_dir / "playout-graph.json",
            stdout_path=request.work_dir / "out.log",
            stderr_path=request.work_dir / "err.log",
            args=("worker",),
        )


def test_encoder_unavailable_airs_the_slate_instead_of_dead_air(tmp_path: Path) -> None:
    """Degraded-mode tier 4 (owner ruling: dead air is the cardinal sin). When
    the egress encoder cannot start for the program source -- e.g. GStreamer
    already fell back to FFmpeg and FFmpeg ALSO failed -- the daemon must air the
    fallback slate (state FALLBACK_SLATE), never drop to ERROR/black."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    strategy = _EncoderUnavailableThenSlateStrategy()

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,  # type: ignore[arg-type]
    )

    assert daemon.process_once("gov") == 1

    state = store.read_state("gov")
    assert state is not None
    # Slate, NOT dead air.
    assert state.state == "FALLBACK_SLATE"
    assert state.state != "ERROR"
    assert "encoder unavailable" in (state.last_error or "")
    # Tried the program first, then the slate.
    assert strategy.start_labels == ["Council meeting", "Fallback slate"]


def test_start_releases_the_prepared_plan_when_the_encoder_falls_back_to_slate(
    tmp_path: Path,
) -> None:
    """Hostile-review follow-up (second pass), item 1: prepare() succeeds
    (mints a real plan_dir for the PROGRAM plan) before the encoder-
    unavailable retry decides to air the fallback slate instead --
    using_fallback_slate flips True AFTER prepare() already ran, so the
    pre-fix guard at the tracking site silently DROPPED the reference
    without ever releasing it. Proves it is released instead."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    strategy = _EncoderUnavailableThenSlateStrategy()
    counter = {"n": 0}
    released: list[Path] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,  # type: ignore[arg-type]
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    # The program plan's prepared directory (plan-1) was minted, then
    # released once the slate aired instead -- never tracked as active
    # (the slate was never built by the preparer), never left dangling.
    assert released == [tmp_path / "plan-1"]
    assert daemon.live_prepared_plan_dirs("gov") == frozenset()


def test_start_tracks_not_releases_the_prepared_plan_for_a_force_fallback_slate(
    tmp_path: Path,
) -> None:
    """Hostile-review follow-up (third pass), P0: ``using_fallback_slate``
    also flips True on THREE paths BEFORE the preparer ever runs --
    ``force_fallback_slate`` (the crash-loop latch), a caption-readiness
    refusal, and ``source_plan is None`` (this test covers the first). On
    all three, the preparer runs against the SLATE plan itself (source_plan
    was already reassigned before the preparer block), so the resulting
    prepared_plan_dir is what the encoder is ACTIVELY airing from -- the
    pre-fix tracking code released (rmtree'd) it out from under the live
    slate the moment it started airing (reviewer-proven with a probe:
    prepared_for=['Fallback slate'], state FALLBACK_SLATE pid=111,
    released=['plan-1']). Proves the directory is tracked as active while
    the slate airs, NOT released, and is only released once that worker
    actually exits."""
    from civiccast.egress.daemon import _LIVE_SOURCE_FAILURE_FALLBACK_STREAK

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    n = _LIVE_SOURCE_FAILURE_FALLBACK_STREAK
    pids = tuple(100 + i for i in range(n + 2))  # 1 initial start + n relaunches + 1 more
    processes = [_FakeProcess(pid=pid, returncode=None) for pid in pids]
    started: list[_FakeProcess] = []
    counter = {"n": 0}
    released: list[Path] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _live_source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
        restart_cooldown_seconds=0.0,  # never defer — isolate the fallback trigger itself
    )

    daemon.process_once("gov")  # start -> the live source, ON_AIR (plan-1, tracked active)
    for _ in range(n):
        started[-1].returncode = 1  # the live source drops / never connects
        daemon.process_once("gov")  # the LAST of these force_fallback_slate=True

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    # The slate's own prepared plan (whatever plan-N the preparer minted on
    # the force_fallback_slate relaunch) is tracked ACTIVE, not released,
    # while this worker airs it.
    active_dir = daemon._active_prepared_plan_dir.get("gov")  # type: ignore[attr-defined]
    assert active_dir is not None
    assert active_dir not in released
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({active_dir})

    # Once THAT worker exits (an operator stop here -- a bare clean exit of a
    # FALLBACK_SLATE worker now relaunches onto the still-resolvable live
    # source instead of going dark, see
    # test_slate_eos_with_a_due_program_relaunches_instead_of_stopping), the
    # slate's own directory is released like any other active plan.
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")
    assert store.read_state("gov").state == "STOPPED"
    assert active_dir in released
    assert daemon.live_prepared_plan_dirs("gov") == frozenset()


def test_start_tracks_not_releases_the_prepared_plan_for_a_caption_readiness_refusal(
    tmp_path: Path,
) -> None:
    """Hostile-review follow-up (third pass), P0 (second early-flip path):
    same defect as the force_fallback_slate test above, triggered instead by
    a caption-readiness refusal that requires the fallback slate."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    counter = {"n": 0}
    released: list[Path] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        caption_readiness_provider=lambda _channel_id: SimpleNamespace(
            ready=False,
            refusal_reason="caption-storage-volumes-diverge",
            requires_fallback_slate=True,
        ),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert released == []  # the slate's own directory (plan-1) is airing, not released
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})

    # An operator stop (a bare clean exit of a FALLBACK_SLATE worker whose
    # program plan still resolves now relaunches instead of going dark --
    # see the force_fallback_slate test above).
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")
    assert store.read_state("gov").state == "STOPPED"
    assert released == [tmp_path / "plan-1"]
    assert daemon.live_prepared_plan_dirs("gov") == frozenset()


def test_start_tracks_not_releases_the_prepared_plan_for_a_missing_source_plan(
    tmp_path: Path,
) -> None:
    """Hostile-review follow-up (third pass), P0 (third early-flip path):
    same defect as the two tests above, triggered instead by
    ``source_plan_provider`` returning ``None`` with a fallback configured."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    counter = {"n": 0}
    released: list[Path] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
    )

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert released == []  # the slate's own directory (plan-1) is airing, not released
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})

    started[0].returncode = 0  # the slate worker exits cleanly
    daemon.process_once("gov")
    assert released == [tmp_path / "plan-1"]
    assert daemon.live_prepared_plan_dirs("gov") == frozenset()


def test_start_releases_the_prepared_plan_on_a_failure_after_prepare_succeeds(
    tmp_path: Path,
) -> None:
    """Hostile-review follow-up (second pass), item 2: ANY raise between a
    successful prepare() and the tracking decision (a provider hook here --
    cg_overlay_provider) used to leak prepared_plan_dir; each retry (e.g. a
    subsequent auto_start pass) would mint another one. Proves the directory
    is released, and that the exception still propagates out of _start
    unchanged (existing outer behavior for an exception this generic --
    logged by process_once's command loop -- is untouched by this fix; the
    fix is scoped to the plan_dir, not to changing what state the row is
    left in)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    processes = [_FakeProcess(pid=111)]
    started: list[_FakeProcess] = []
    counter = {"n": 0}
    released: list[Path] = []

    def _raising_cg_overlay_provider(_channel_id: str, _config: EgressConfig) -> Path | None:
        raise RuntimeError("simulated board-overlay provider failure")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan(tmp_path),
        encoder_strategy=_FakeContentReloadStrategy(processes, started),
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
        cg_overlay_provider=_raising_cg_overlay_provider,
    )

    daemon.process_once("gov")

    assert released == [tmp_path / "plan-1"]  # released, not leaked
    assert daemon.live_prepared_plan_dirs("gov") == frozenset()
    assert len(started) == 0  # the encoder never even started
    assert daemon._active_prepared_plan_dir.get("gov") is None  # never tracked as active


def test_unexpected_encoder_start_failure_does_not_claim_a_running_slate(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    failures: list[Exception] = []

    class _BrokenEncoder:
        name = "broken-encoder"
        supports_live_swap = False
        supports_content_reload = False
        starts = 0

        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            self.starts += 1
            raise ModuleNotFoundError("No module named 'gi'")

    encoder = _BrokenEncoder()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: None,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=encoder,  # type: ignore[arg-type]
        command_failure_hook=lambda _cid, _cmd, exc: failures.append(exc),
    )

    assert daemon.process_once("gov") == 1
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ERROR"
    assert "No module named 'gi'" in (state.last_error or "")
    assert state.pid is None
    assert store.recent_health("gov", 1)[0].state == "ERROR"
    assert len(failures) == 1 and isinstance(failures[0], ModuleNotFoundError)
    assert daemon.process_once("gov") == 0
    assert encoder.starts == 1  # command remains consumed; no new retry policy


def test_encoder_unavailable_with_no_fallback_provider_is_error_not_a_crash(
    tmp_path: Path,
) -> None:
    """With no fallback provider there is no slate to air, so the channel goes
    to ERROR -- but the daemon still never raises out of process_once."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    class _AlwaysUnavailable:
        name = "always-unavailable"
        supports_live_swap = False
        supports_content_reload = False

        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            from civiccast.egress.errors import EncoderUnavailableError

            raise EncoderUnavailableError("no usable egress encoder")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        encoder_strategy=_AlwaysUnavailable(),  # type: ignore[arg-type]
    )

    assert daemon.process_once("gov") == 1
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ERROR"


class _AlwaysFfmpegNotFoundStrategy:
    """Every ``start()`` raises ``FfmpegNotFoundError``, regardless of which
    source plan (program or slate) is requested. This mirrors the REAL
    ``ConcatEncoderStrategy`` in production: its ``FfmpegNotFoundError`` comes
    from ``civiccast.stream._ffmpeg._ffmpeg_path()``, a pure
    ``shutil.which("ffmpeg")`` PATH lookup that is completely independent of
    the content being encoded, so it is deterministic across calls within one
    process. (P1 audit finding on an earlier version of this test file: a fake
    strategy that raised for the program plan but *succeeded* for the slate
    plan modeled a state the real ``ConcatEncoderStrategy`` can never enter --
    a passing test that proved nothing about production behavior.)"""

    name = "fake-always-ffmpeg-not-found"
    supports_live_swap = False
    supports_content_reload = False

    def __init__(self) -> None:
        self.start_labels: list[str] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        from civiccast.stream._ffmpeg import FfmpegNotFoundError

        self.start_labels.append(request.source_plan.segments[0].label)
        raise FfmpegNotFoundError(
            "ffmpeg not found on PATH. Install or repair the bundled FFmpeg "
            "runtime and verify it with 'civiccast doctor'."
        )


class _FakeIndependentSlateStrategy:
    """Models ``GstPlayoutStrategy`` standing in as the K2-1 follow-up's
    genuinely separate, ffmpeg-PATH-independent last-resort encoder: a
    DIFFERENT strategy instance from the one that raised ``FfmpegNotFoundError``,
    injected via ``independent_slate_strategy_factory`` rather than by making
    the SAME fake strategy behave inconsistently."""

    name = "fake-independent-slate-encoder"
    supports_live_swap = False
    supports_content_reload = False

    def __init__(self) -> None:
        self.start_labels: list[str] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        self.start_labels.append(request.source_plan.segments[0].label)
        return EncoderStartResult(
            process=_FakeProcess(pid=516),
            concat_plan_path=request.work_dir / "playout-graph.json",
            stdout_path=request.work_dir / "out.log",
            stderr_path=request.work_dir / "err.log",
            args=("worker",),
        )


def test_ffmpeg_not_found_airs_the_slate_instead_of_dead_air(tmp_path: Path) -> None:
    """Audit K2-1 (MAJOR, release-blocking) + P1 follow-up: a missing ffmpeg
    binary for the program source must land the channel on the fallback slate
    (state FALLBACK_SLATE), with the same operator-visible degradation event
    (last_error + a FALLBACK_SLATE proof event + health sample) as any other
    tier transition -- never ERROR/dead air while an independent slate encoder
    is available. The recovery must come from a genuinely SEPARATE encoder
    (``independent_slate_strategy_factory``, standing in for the real
    ``GstPlayoutStrategy``) -- retrying the SAME ffmpeg-concat strategy can
    never succeed, since its FfmpegNotFoundError is a content-independent PATH
    lookup (see ``_AlwaysFfmpegNotFoundStrategy``)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    strategy = _AlwaysFfmpegNotFoundStrategy()
    independent_strategy = _FakeIndependentSlateStrategy()

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,  # type: ignore[arg-type]
        independent_slate_strategy_factory=lambda: independent_strategy,  # type: ignore[arg-type, return-value]
    )

    assert daemon.process_once("gov") == 1

    state = store.read_state("gov")
    assert state is not None
    # Slate, NOT dead air.
    assert state.state == "FALLBACK_SLATE"
    assert state.state != "ERROR"
    assert "ffmpeg not found" in (state.last_error or "").lower()
    # The program tier was tried once (and failed) against the ffmpeg-concat
    # strategy; the slate tier was tried against the SEPARATE independent
    # encoder, not a second call to the strategy already known to be unusable.
    assert strategy.start_labels == ["Council meeting"]
    assert independent_strategy.start_labels == ["Fallback slate"]

    proof_events = store.recent_proof_events("gov", 1)
    assert proof_events[0].state == "FALLBACK_SLATE"
    assert store.recent_health("gov", 1)[0].state == "FALLBACK_SLATE"


def test_ffmpeg_not_found_with_no_fallback_provider_is_error_not_a_crash(
    tmp_path: Path,
) -> None:
    """With no fallback provider there is no slate to air, so the channel goes
    to ERROR -- but the daemon still never raises out of process_once."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    class _AlwaysFfmpegNotFound:
        name = "always-ffmpeg-not-found"
        supports_live_swap = False
        supports_content_reload = False

        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            from civiccast.stream._ffmpeg import FfmpegNotFoundError

            raise FfmpegNotFoundError("ffmpeg not found on PATH.")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        encoder_strategy=_AlwaysFfmpegNotFound(),  # type: ignore[arg-type]
    )

    assert daemon.process_once("gov") == 1
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ERROR"


def test_ffmpeg_not_found_on_slate_too_is_the_true_zero_ffmpeg_floor(
    tmp_path: Path,
) -> None:
    """Documents the true zero-anything floor (K2 design: a no-crash state with
    operator alerting). If ffmpeg is genuinely absent from the machine AND no
    independent encoder (GStreamer) is available in this deployment either
    (``independent_slate_strategy_factory`` returns None, exactly as the real
    ``_default_independent_slate_strategy`` does when the gst package is not
    importable), the slate retry falls back to the SAME broken ffmpeg-concat
    strategy and fails too. The ladder must still land the channel on ERROR
    (not raise out of process_once, not silently hang) with last_error naming
    the real cause, after having genuinely attempted the slate tier first."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    strategy = _AlwaysFfmpegNotFoundStrategy()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,  # type: ignore[arg-type]
        independent_slate_strategy_factory=lambda: None,
    )

    assert daemon.process_once("gov") == 1
    # The ladder genuinely tried both tiers before giving up -- no independent
    # encoder was available, so the slate retry went through the same
    # ffmpeg-concat strategy as the program attempt.
    assert strategy.start_labels == ["Council meeting", "Fallback slate"]

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ERROR"
    assert "ffmpeg not found" in (state.last_error or "").lower()
    assert store.recent_health("gov", 1)[0].state == "ERROR"


def test_default_independent_slate_strategy_is_none_when_gst_not_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production default factory must never raise -- an unavailable
    GStreamer package in this deployment is exactly the expected "no
    independent fallback" case, not an error."""
    import builtins

    from civiccast.egress.daemon import _default_independent_slate_strategy

    real_import = builtins.__import__

    def _blocked_import(
        name: str,
        globals: Any = None,  # noqa: A002 - matches builtins.__import__'s own parameter name
        locals: Any = None,  # noqa: A002 - matches builtins.__import__'s own parameter name
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        if name == "civiccast.egress.gst.strategy" or name.startswith(
            "civiccast.egress.gst.strategy."
        ):
            raise ImportError(f"simulated: {name} not installed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    assert _default_independent_slate_strategy() is None


# --- BLOCKER B1: a live source that keeps crashing the worker must land on slate,
# --- never crash-loop against the same dead source forever ("dead air is NEVER
# --- acceptable" — see the encoder-unavailable comment in daemon.py). --------------


def test_repeated_live_source_crash_falls_back_to_slate_instead_of_dead_air_loop(
    tmp_path: Path,
) -> None:
    """A live SRT/UDP/RTSP source that is unreachable or drops crashes the
    worker immediately after each relaunch — the encoder process itself starts
    fine (so ``_start``'s own EncoderUnavailableError/FfmpegNotFoundError
    fallback-to-slate seam never fires), then dies inside the pipeline once it
    can't connect to / keep reading the source. Before this fix,
    ``_relaunch_after_crash`` paced the RATE of relaunch but always relaunched
    against the SAME source_plan_provider, so this was an infinite crash-loop
    with no terminal slate state — dead air forever. After
    ``_LIVE_SOURCE_FAILURE_FALLBACK_STREAK`` consecutive crash-relaunches that
    never once reached a healthy uptime, the daemon must stop trusting the
    configured live source and force the fallback-slate path instead."""
    from civiccast.egress.daemon import _LIVE_SOURCE_FAILURE_FALLBACK_STREAK

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    n = _LIVE_SOURCE_FAILURE_FALLBACK_STREAK
    pids = tuple(100 + i for i in range(n + 1))  # 1 initial start + n relaunches
    processes = [_FakeProcess(pid=pid, returncode=None) for pid in pids]
    started: list[_FakeProcess] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _live_source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        restart_cooldown_seconds=0.0,  # never defer — isolate the fallback trigger itself
    )

    daemon.process_once("gov")  # start → the live source, ON_AIR
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Live: Council chamber"

    for _ in range(n):
        started[-1].returncode = 1  # the live source drops / never connects
        daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    # The terminal state that replaces dead air: slate, not a channel still
    # silently crash-looping against the same unreachable source.
    assert state.state == "FALLBACK_SLATE"
    assert state.current_source_label == "Fallback slate"
    assert state.last_error is not None
    assert str(n) in state.last_error
    assert len(started) == n + 1  # every relaunch actually tried a fresh process
    assert store.recent_health("gov", 1)[0].state == "FALLBACK_SLATE"


def test_live_source_fallback_latches_stably_even_if_the_slate_encoder_also_crashes(
    tmp_path: Path,
) -> None:
    """Once the crash streak has forced the channel onto the fallback slate,
    a further crash of THAT slate encoder must relaunch the slate again — not
    bounce back to re-resolving the still-dead live source via
    ``source_plan_provider`` for one attempt before flapping back to slate on
    the next crash. The streak only clears on a healthy uptime or an explicit
    operator/automation command (see ``_begin_relaunch``'s docstring), so the
    channel stays latched onto a stable slate state across repeat crashes of
    the slate encoder itself, instead of an ON_AIR/FALLBACK_SLATE flap."""
    from civiccast.egress.daemon import _LIVE_SOURCE_FAILURE_FALLBACK_STREAK

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    n = _LIVE_SOURCE_FAILURE_FALLBACK_STREAK
    # 1 initial start + n relaunches to reach slate + n more relaunches of the
    # (also crashing) slate encoder itself.
    pids = tuple(100 + i for i in range(2 * n + 1))
    processes = [_FakeProcess(pid=pid, returncode=None) for pid in pids]
    started: list[_FakeProcess] = []

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _live_source_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        restart_cooldown_seconds=0.0,
    )

    daemon.process_once("gov")
    for _ in range(n):
        started[-1].returncode = 1
        daemon.process_once("gov")
    assert store.read_state("gov").state == "FALLBACK_SLATE"

    for _ in range(n):
        started[-1].returncode = 1  # the slate encoder itself keeps crashing too
        daemon.process_once("gov")
        # Every relaunch in this loop stayed on slate — none of them ever
        # bounced back to ON_AIR against the still-dead live source.
        state = store.read_state("gov")
        assert state is not None
        assert state.state == "FALLBACK_SLATE"
        assert state.current_source_label == "Fallback slate"

    assert len(started) == 2 * n + 1


# --- MAJOR M1: an HLS relay child death must surface in health, not stay invisible --


def test_dead_hls_relay_overrides_sink_health_to_false_on_the_next_daemon_tick(
    tmp_path: Path,
) -> None:
    """Before this fix, nothing polled the HLS relay subprocess after start:
    ``/api/staff/egress/channels/{id}/health`` derived the ``hls`` sink's
    ``connected`` flag purely from the MAIN encoder's own UDP send progress
    (``build_default_sink_health`` / an injected ``sink_health_provider``),
    blind to whether the SEPARATE relay child (disk full / ffmpeg missing /
    OOM) was still alive. This proves the daemon now polls
    ``HlsRelaySupervisor.is_alive`` every ``process_once`` tick and overrides
    the hls sink to unhealthy the moment the relay is confirmed dead — even
    though the injected health provider below always claims "connected", so
    the assertion can only pass if the daemon's own liveness poll is what
    flipped it."""
    from civiccast.egress.hls_relay import HlsRelaySupervisor

    store = InMemoryEgressStore()
    hls_sink = EgressSinkSpec(kind="hls", label="Web", uri=str(tmp_path / "hls-live"))
    store.upsert_config(
        EgressConfig(channel_id="gov", enabled=True, slate_message="slate", sinks=[hls_sink])
    )
    store.enqueue_command(_command())

    relay_procs: list[_FakeProcess] = []

    def relay_starter(_args: list[str], *, stderr_path: Path | None = None) -> _FakeProcess:
        proc = _FakeProcess(pid=999)
        relay_procs.append(proc)
        return proc

    hls_relay = HlsRelaySupervisor(starter=relay_starter)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(pid=4242, returncode=None),
        hls_relay_supervisor=hls_relay,
        # Always claims "connected" — proves the override, not the provider,
        # is what produces the False below.
        sink_health_provider=lambda _channel_id, _config, _metrics: {"Web": True},
    )

    assert daemon.process_once("gov") == 1
    assert len(relay_procs) == 1  # the relay was actually created for the hls sink
    assert store.recent_health("gov", 1)[0].sink_connected == {"Web": True}

    relay_procs[0].returncode = 1  # the relay child dies on its own between ticks
    daemon.process_once("gov")  # a routine tick — the main encoder is unaffected

    health = store.recent_health("gov", 1)[0]
    assert health.sink_connected == {"Web": False}


def test_hls_relay_health_override_is_not_applied_when_relay_is_still_alive(
    tmp_path: Path,
) -> None:
    """Negative case for the same seam: a live relay must never be forced
    unhealthy — the override is death-specific, not a blanket downgrade."""
    from civiccast.egress.hls_relay import HlsRelaySupervisor

    store = InMemoryEgressStore()
    hls_sink = EgressSinkSpec(kind="hls", label="Web", uri=str(tmp_path / "hls-live"))
    store.upsert_config(
        EgressConfig(channel_id="gov", enabled=True, slate_message="slate", sinks=[hls_sink])
    )
    store.enqueue_command(_command())

    hls_relay = HlsRelaySupervisor(starter=lambda _args, *, stderr_path=None: _FakeProcess(pid=999))

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(pid=4242, returncode=None),
        hls_relay_supervisor=hls_relay,
        sink_health_provider=lambda _channel_id, _config, _metrics: {"Web": True},
    )

    daemon.process_once("gov")
    daemon.process_once("gov")  # a second, routine tick — relay never died

    assert store.recent_health("gov", 1)[0].sink_connected == {"Web": True}


def test_daemon_records_the_source_plan_it_actually_dispatched(tmp_path: Path) -> None:
    """Hostile-review (d): channel automation's rollover pass needs to know when the
    AIRING plan runs out, and the only honest source for that is the plan the daemon
    actually sent. Before this, automation re-called the source plan provider and
    summed whatever came back -- a different, re-windowed segment list that can put
    the projected end (and therefore the rollover trigger) past the real one.

    The record is keyed by the proof event written alongside it, so a consumer can
    tell "this is what is on air" from "this is stale"."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    assert daemon.dispatched_plan_horizon("gov") is None
    assert daemon.process_once("gov") == 1

    state = store.read_state("gov")
    assert state is not None
    recorded = daemon.dispatched_plan_horizon("gov")
    assert recorded is not None
    proof_event_id, durations, switch_deferred = recorded
    assert proof_event_id == state.current_proof_event_id
    assert durations == tuple(
        float(segment.duration_seconds) for segment in _source_plan(tmp_path).segments
    )
    # A START puts its plan on air immediately -- only a content-reload can defer.
    assert switch_deferred is False


def test_child_stderr_tail_is_ascii_folded_before_it_reaches_last_error(tmp_path: Path) -> None:
    """T6 soak 2026-09-05 (kit e502074, Desktop/CIVICCAST-EVIDENCE/
    soak-120-e502074-20260905): the worker's stall line carried one non-ASCII
    character, came back from the child log as U+FFFD (the tail reader uses
    ``errors="replace"``), was folded into ``last_error`` and then failed the
    Postgres state write with ``UnicodeEncodeError: 'charmap' codec can't encode
    character '\ufffd'``. That exception escaped ``process_once`` and aborted
    ``ChannelAutomationService._run_channel_pass`` before ``_check_plan_rollover``
    ever ran -- 23 aborted passes, zero rollovers, in a 2h soak.

    The tail must therefore be ASCII by the time it can be persisted.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )
    log_path = tmp_path / "gov-stderr.log"
    log_path.write_text(
        "CTRL stall: no output for 10s � quitting for daemon restart\n",
        encoding="utf-8",
    )
    daemon._stderr_logs["gov"] = log_path

    tail = daemon._child_stderr_tail("gov")
    assert tail is not None
    assert "�" not in tail
    tail.encode("cp1252")  # the client encoding that raised in the soak

    message = daemon._child_exit_error("gov", suffix="restarting.")
    assert "�" not in message
    message.encode("cp1252")
    assert "CTRL stall: no output for 10s" in message


def test_child_stderr_tail_excludes_bytes_written_before_this_worker_spawned(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )
    log_path = tmp_path / "gov-stderr.log"
    old_line = "CTRL stall: stale worker failure\n"
    log_path.write_text(old_line, encoding="utf-8")
    daemon._stderr_logs["gov"] = log_path
    daemon._stderr_spawn_offset["gov"] = log_path.stat().st_size
    daemon._stderr_spawn_identity["gov"] = (
        log_path.stat().st_dev,
        log_path.stat().st_ino,
    )
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write("current worker diagnostic\n")

    tail = daemon._child_stderr_tail("gov")

    assert tail is not None
    assert "current worker diagnostic" in tail
    assert "stale worker failure" not in tail


def test_on_air_evidence_restarts_its_offset_after_stderr_rotation(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )
    log_path = tmp_path / "gov-stderr.log"
    log_path.write_text("x" * 700, encoding="utf-8")
    prior_identity = (log_path.stat().st_dev, log_path.stat().st_ino)
    daemon._stderr_logs["gov"] = log_path
    daemon._stderr_spawn_offset["gov"] = log_path.stat().st_size
    daemon._stderr_spawn_identity["gov"] = prior_identity
    daemon._processes["gov"] = _FakeProcess(pid=4242)

    log_path.rename(tmp_path / "gov-stderr.log.1")
    log_path.write_text(
        "CTRL first-output: first buffer after 0.1s pid=4242\n" + "y" * 700,
        encoding="utf-8",
    )

    assert log_path.stat().st_size > daemon._stderr_spawn_offset["gov"]
    assert daemon._observed_on_air_evidence("gov") is True


def test_operator_stop_blocks_recovery_start_until_explicit_start(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    workers: list[_FakeProcess] = []

    def starter(_args: list[str]) -> _FakeProcess:
        worker = _FakeProcess(pid=5100 + len(workers))
        workers.append(worker)
        return worker

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=starter,
    )
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")

    assert daemon.has_operator_stop_intent("gov") is True

    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 12, 1, tzinfo=UTC),
            issued_by="restart-recovery",
            command_id="restart-recovery-ON_AIR-test",
        )
    )
    daemon.process_once("gov")

    assert daemon.has_operator_stop_intent("gov") is True
    assert workers == []

    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 12, 2, tzinfo=UTC),
            issued_by="operator",
            command_id="operator-start",
        )
    )
    daemon.process_once("gov")

    assert daemon.has_operator_stop_intent("gov") is False
    assert len(workers) == 1


def test_current_source_label_with_an_accented_title_survives_a_cp1252_write(
    tmp_path: Path,
) -> None:
    """Persistence-boundary regression: an operator-entered title with an
    accent (the common case db_safe_text is meant to PRESERVE, not just avoid
    crashing on) must round-trip through a store that encodes to cp1252 on
    write -- exactly what a WIN1252-encoded Postgres cluster does."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    def _cp1252_write_state(row: EgressStateRow) -> None:
        # Simulate the persistence boundary a real WIN1252 psycopg connection
        # enforces: anything not cp1252-encodable raises, exactly like the
        # T6 soak's UnicodeEncodeError.
        if row.current_source_label is not None:
            row.current_source_label.encode("cp1252")
        if row.last_error is not None:
            row.last_error.encode("cp1252")
        InMemoryEgressStore.write_state(store, row)

    store.write_state = _cp1252_write_state  # type: ignore[method-assign]

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan_with_label(
            tmp_path, "Réunion du conseil municipal"
        ),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    daemon.process_once("gov")  # must not raise

    state = store.read_state("gov")
    assert state is not None
    assert state.current_source_label is not None
    # cp1252 CAN represent e-acute -- db_safe_text must not degrade it.
    assert "Réunion" in state.current_source_label


def test_last_error_with_the_unicode_replacement_character_survives_a_cp1252_write(
    tmp_path: Path,
) -> None:
    """last_error=str(exc) (daemon.py's zero-ffmpeg-floor ERROR handler) must
    also survive a WIN1252 write -- U+FFFD is the exact character the T6 soak
    hit, and cp1252 genuinely cannot represent it (unlike an accent), so this
    proves the degrade-not-crash half of db_safe_text's contract."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    def _cp1252_write_state(row: EgressStateRow) -> None:
        if row.last_error is not None:
            row.last_error.encode("cp1252")
        InMemoryEgressStore.write_state(store, row)

    store.write_state = _cp1252_write_state  # type: ignore[method-assign]

    class _AlwaysUnavailableWithReplacementChar:
        name = "always-unavailable"
        supports_live_swap = False
        supports_content_reload = False

        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            from civiccast.egress.errors import EncoderUnavailableError

            # The exact character shape the T6 soak hit: a stray U+FFFD from
            # a child log read with errors="replace".
            raise EncoderUnavailableError("worker stall: no output for 10s � quitting")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        encoder_strategy=_AlwaysUnavailableWithReplacementChar(),  # type: ignore[arg-type]
    )

    daemon.process_once("gov")  # must not raise

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ERROR"
    assert state.last_error is not None
    assert "�" not in state.last_error
    state.last_error.encode("cp1252")


def test_proof_event_label_and_summary_survive_a_cp1252_write(tmp_path: Path) -> None:
    """_build_proof_event's source_label/machine_summary go through
    EgressStore.append_proof_event, a SEPARATE write path from
    EgressStore.write_state -- its own persistence-boundary regression."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    def _cp1252_append_proof_event(event: Any) -> None:
        event.source_label.encode("cp1252")
        event.machine_summary.encode("cp1252")
        InMemoryEgressStore.append_proof_event(store, event)

    store.append_proof_event = _cp1252_append_proof_event  # type: ignore[method-assign]

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan_with_label(
            tmp_path, "Réunion du conseil municipal"
        ),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    daemon.process_once("gov")  # must not raise

    proof_events = store.recent_proof_events("gov", 1)
    assert proof_events[0].source_label is not None
    assert "Réunion" in proof_events[0].source_label


def test_process_once_isolates_a_poll_failure_from_the_rest_of_the_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The three per-tick polls (_poll_hls_relay, _poll_process,
    _service_backoff_relaunch) must be isolated from each other AND from
    command draining, mirroring the per-command guard immediately below them
    in process_once. Before this fix, one poll raising (e.g. the exact
    UnicodeEncodeError the encoding fix closes, or any other write failure)
    escaped process_once and aborted the WHOLE pass -- pop_pending_commands
    never even ran, so a queued takeover/stop sat unprocessed too."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    calls: list[str] = []
    real_poll_process = daemon._poll_process
    real_backoff = daemon._service_backoff_relaunch

    def _boom(_channel_id: str) -> None:
        calls.append("hls_relay")
        raise RuntimeError("simulated write failure")

    def _tracked_poll_process(channel_id: str) -> None:
        calls.append("poll_process")
        real_poll_process(channel_id)

    def _tracked_backoff(channel_id: str) -> None:
        calls.append("backoff")
        real_backoff(channel_id)

    monkeypatch.setattr(daemon, "_poll_hls_relay", _boom)
    monkeypatch.setattr(daemon, "_poll_process", _tracked_poll_process)
    monkeypatch.setattr(daemon, "_service_backoff_relaunch", _tracked_backoff)

    # Must not raise, and the queued start command must still be processed.
    processed = daemon.process_once("gov")

    assert calls == ["hls_relay", "poll_process", "backoff"]
    assert processed == 1
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"


# ---------------------------------------------------------------------------
# Clean-machine walkthrough of beta.5 (2026-09-09 MDT): a finite fallback
# slate reaching EOS at a program boundary went STOPPED instead of airing the
# due program, and a manual Start read Stopped until the prepare finished.
# ---------------------------------------------------------------------------


def _slate_then_program_daemon(
    tmp_path: Path,
    *,
    program_plan: list[EgressSourcePlan | None],
    processes: list[_FakeProcess],
    started: list[_FakeProcess],
    clock: list[float],
    source_preparer: Callable[[EgressSourcePlan, EgressConfig], SourcePreparationReport]
    | None = None,
    prepared_plan_release: Callable[[Path], None] | None = None,
    restart_cooldown_seconds: float = 0.0,
    slate_plan: Callable[[Path], EgressSourcePlan] = _slate_plan,
    program_resolves: list[int] | None = None,
    channel_start_hook: Callable[[str], None] | None = None,
) -> EgressDaemon:
    """A daemon whose program provider hands out ``program_plan[0]`` (mutable
    by the test) and whose fallback provider always yields the slate plan.
    ``program_resolves`` (a one-element counter) is bumped on every program
    provider call so a test can pin how many times a tick resolved it."""

    def provide(_channel_id: str) -> EgressSourcePlan | None:
        if program_resolves is not None:
            program_resolves[0] += 1
        return program_plan[0]

    return EgressDaemon(
        InMemoryEgressStore(),
        work_dir=tmp_path,
        source_plan_provider=provide,
        fallback_source_provider=lambda _config: slate_plan(tmp_path),
        ffmpeg_starter=lambda _args: _start_fake_process(processes, started),
        source_preparer=source_preparer,
        prepared_plan_release=prepared_plan_release,
        monotonic=lambda: clock[0],
        restart_cooldown_seconds=restart_cooldown_seconds,
        channel_start_hook=channel_start_hook,
    )


def _realistic_slate_plan(tmp_path: Path) -> EgressSourcePlan:
    """A fallback plan of the length the real bulletin filler actually mints:
    ``BulletinFillerSourceGenerator._plan_with_cycle`` caps a plan at
    ``MAX_PLAYLIST_SUBCHAINS`` (12) sub-chains of ``_SLIDE_SECONDS`` (10s)
    -- 120s -- so a boundary that keeps failing recurs every ~2 minutes,
    never inside a 30s window (round-2 hostile review, item 2)."""
    from civiccast.egress.bulletin_filler import _SLIDE_SECONDS
    from civiccast.egress.models import MAX_PLAYLIST_SUBCHAINS

    source = tmp_path / "slate.ts"
    source.write_text("slate", encoding="utf-8")
    segment = EgressSourceSegment(
        label="Fallback slate",
        path=str(source),
        duration_seconds=_SLIDE_SECONDS,
        source_ref="civiccast-slate",
    )
    plan = EgressSourcePlan(channel_id="gov", segments=[segment] * MAX_PLAYLIST_SUBCHAINS)
    assert sum(s.duration_seconds for s in plan.segments) == 120
    return plan


def _record_state_rows(store: InMemoryEgressStore) -> list[tuple[str, str | None]]:
    """Spy on ``store.write_state`` -- the store keeps only the latest row, but
    the ORDER of (state, label) writes is what the STARTING-label tests pin."""
    rows: list[tuple[str, str | None]] = []
    original = store.write_state

    def spy(row: EgressStateRow) -> None:
        rows.append((row.state, row.current_source_label))
        original(row)

    store.write_state = spy  # type: ignore[method-assign]
    return rows


def test_slate_eos_with_a_due_program_relaunches_instead_of_stopping(tmp_path: Path) -> None:
    """Walkthrough 22:00:23: slate worker exits 0 at the boundary (its plan is
    finite to the next due item), no reload is pending (the automation's
    reload prepare was still running), and the program provider now yields
    the due program -- the channel must go ON_AIR on that program, not
    STOPPED, with the slate->program transition recorded.

    Round-2 hostile review: also pins that the exited slate's prepared plan
    directory is released on this path (item 7), that the program plan is
    resolved ONCE on the relaunch tick and threaded into ``_start`` rather
    than re-resolved (item 6), and that the program's label survives every
    STARTING rewrite through to TRANSITIONING (item 5)."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    counter = {"n": 0}
    released: list[Path] = []
    program_resolves = [0]
    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        source_preparer=_prepare_with_tracked_plan_dirs(tmp_path, counter),
        prepared_plan_release=released.append,
        program_resolves=program_resolves,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # nothing due -> FALLBACK_SLATE (process 111)
    assert store.read_state("gov").state == "FALLBACK_SLATE"
    active_dir = daemon._active_prepared_plan_dir.get("gov")  # type: ignore[attr-defined]
    assert active_dir is not None and active_dir not in released

    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")  # now due
    started[0].returncode = 0  # the finite slate plan reached EOS
    rows = _record_state_rows(store)
    program_resolves[0] = 0
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Council meeting"
    assert state.pid == 222
    assert [p.pid for p in started] == [111, 222]
    transition = store.recent_proof_events("gov", 1)[0]
    assert "exited fallback slate" in transition.machine_summary
    assert store.recent_health("gov", 1)[0].state == "ON_AIR"
    assert active_dir in released  # item 7: the slate's plan dir is reclaimed
    assert program_resolves[0] == 1  # item 6: probed once, threaded into _start
    # Item 5: once the program plan is known, every STARTING rewrite and the
    # TRANSITIONING row carry its label -- no row in between says None.
    assert rows[0] == ("STARTING", "Fallback slate")  # the relaunch's pid-clearing write
    assert rows[1:] == [
        ("STARTING", "Council meeting"),  # before prepare
        ("STARTING", "Council meeting"),  # after prepare, before the encoder starts
        ("TRANSITIONING", "Council meeting"),
        ("ON_AIR", "Council meeting"),  # written before the encoder spawns (pid None)
        ("ON_AIR", "Council meeting"),  # and again with the pid
    ]


def test_slate_eos_with_nothing_due_still_stops(tmp_path: Path) -> None:
    """Belt only: with no program to relaunch onto the clean-exit branch
    behaves exactly as before (STOPPED, no last_error, no second worker)."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    daemon = _slate_then_program_daemon(
        tmp_path, program_plan=program_plan, processes=processes, started=started, clock=clock
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")
    assert store.read_state("gov").state == "FALLBACK_SLATE"

    started[0].returncode = 0
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state.state == "STOPPED"
    assert state.last_error is None
    assert [p.pid for p in started] == [111]


def test_slate_eos_relaunch_is_capped_to_one_per_boundary(tmp_path: Path) -> None:
    """The program plan resolves but its prepare keeps failing, so ``_start``
    falls straight back to a (finite) slate that ends at once. The first slate
    EOS relaunches; the second consecutive one must NOT relaunch again --
    STOPPED with a last_error saying why -- and an operator start afterwards
    resets the count so a fresh boundary gets its one relaunch again."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=pid) for pid in (111, 222, 333)]
    started: list[_FakeProcess] = []
    clock = [1000.0]

    def prepare(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        if source_plan.segments[0].label != "Fallback slate":
            raise SourcePrepareError("conform failed: unsupported codec")
        return SourcePreparationReport(source_plan=source_plan, records=())

    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        source_preparer=prepare,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # FALLBACK_SLATE (111)
    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")

    started[0].returncode = 0  # boundary: slate EOS #1 -> one automatic relaunch
    clock[0] += 5.0
    daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "FALLBACK_SLATE"  # the program's prepare failed -> slate again
    assert state.pid == 222
    assert "conform failed" in (state.last_error or "")

    started[1].returncode = 0  # slate EOS #2, 10s later: the cap refuses a loop
    clock[0] += 10.0
    daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "STOPPED"
    assert "stopped instead of looping" in (state.last_error or "")
    assert [p.pid for p in started] == [111, 222]
    assert store.recent_health("gov", 1)[0].state == "STOPPED"

    # A later boundary (past the window) gets its own single relaunch.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-restart",
        )
    )
    program_plan[0] = None
    daemon.process_once("gov")  # FALLBACK_SLATE again (333) via the operator start
    assert store.read_state("gov").state == "FALLBACK_SLATE"
    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")
    processes.append(_FakeProcess(pid=444))
    started[2].returncode = 0
    clock[0] += 60.0
    daemon.process_once("gov")
    assert store.read_state("gov").pid == 444  # relaunched once more


def test_slate_eos_after_an_operator_drain_does_not_relaunch(tmp_path: Path) -> None:
    """A drain is the operator taking the channel off air on purpose; the
    slate ending under a drain must still land STOPPED even when a program
    is due."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    daemon = _slate_then_program_daemon(
        tmp_path, program_plan=program_plan, processes=processes, started=started, clock=clock
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")
    assert store.read_state("gov").state == "FALLBACK_SLATE"

    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")
    store.enqueue_command(_command("drain"))
    daemon.process_once("gov")  # DRAINING; the fake process keeps running
    assert store.read_state("gov").state == "DRAINING"
    started[0].returncode = 0
    daemon.process_once("gov")

    assert store.read_state("gov").state == "STOPPED"
    assert [p.pid for p in started] == [111]


def test_start_writes_starting_with_the_target_label_before_source_preparation(
    tmp_path: Path,
) -> None:
    """Walkthrough 22:00:39-22:01:03: a manual Start read Stopped for ~20s
    because ``_start`` wrote nothing until the preparer returned. The row
    must already say STARTING (with the program's label, no pid) at the
    moment the preparer is called."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="STOPPED",
            current_source_label=None,
            current_proof_event_id=None,
            updated_at=datetime(2026, 6, 5, 11, 0, tzinfo=UTC),
            pid=None,
        )
    )
    store.enqueue_command(_command())
    seen_at_prepare: list[EgressStateRow | None] = []

    def prepare(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        seen_at_prepare.append(store.read_state("gov"))
        return SourcePreparationReport(source_plan=source_plan, records=())

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan_with_label(
            tmp_path, "Council meeting"
        ),
        source_preparer=prepare,
        ffmpeg_starter=lambda _args: _FakeProcess(pid=111),
    )
    rows = _record_state_rows(store)
    daemon.process_once("gov")

    assert len(seen_at_prepare) == 1
    row = seen_at_prepare[0]
    assert row is not None
    assert row.state == "STARTING"
    assert row.current_source_label == "Council meeting"
    assert row.pid is None
    final = store.read_state("gov")
    assert final.state == "ON_AIR"
    assert final.pid == 111
    # Round-2 hostile review, item 5: the post-prepare STARTING rewrite used
    # to drop the label (``_write_state`` replaces the row) -- every STARTING
    # row this start wrote carries it.
    starting_rows = [label for state, label in rows if state == "STARTING"]
    assert starting_rows == ["Council meeting", "Council meeting"]


def test_start_leaves_the_early_slate_flip_state_in_place_during_preparation(
    tmp_path: Path,
) -> None:
    """The early flips to FALLBACK_SLATE (no program plan here) already carry
    a last_error explaining the slate; the new STARTING write must not
    overwrite that while the slate itself is prepared."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    seen_at_prepare: list[EgressStateRow | None] = []

    def prepare(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        seen_at_prepare.append(store.read_state("gov"))
        return SourcePreparationReport(source_plan=source_plan, records=())

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        source_preparer=prepare,
        ffmpeg_starter=lambda _args: _FakeProcess(pid=111),
    )
    daemon.process_once("gov")

    assert len(seen_at_prepare) == 1
    row = seen_at_prepare[0]
    assert row is not None
    assert row.state == "FALLBACK_SLATE"
    assert "No valid source plan" in (row.last_error or "")
    assert store.read_state("gov").state == "FALLBACK_SLATE"


# ---------------------------------------------------------------------------
# Round-2 hostile review of the slate-EOS relaunch (PR #212).
# ---------------------------------------------------------------------------


def test_persistently_unplayable_program_stops_after_the_cap_and_does_not_flap(
    tmp_path: Path,
) -> None:
    """Item 2 (HIGH). The real bulletin filler mints a slate plan of at most
    120s (``_realistic_slate_plan``), so the first-round 30s time window
    could never refuse anything: a program whose prepare always fails would
    have flapped slate -> relaunch -> slate every ~2 minutes forever. With
    the cap a COUNT of consecutive automatic relaunches, the channel gets
    exactly one, then STOPPED with the operator-facing last_error, and stays
    STOPPED across further ticks (no third worker, no more prepares)."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=pid) for pid in (111, 222, 333, 444)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    prepares: list[str] = []

    def prepare(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepares.append(source_plan.segments[0].label)
        if source_plan.segments[0].label != "Fallback slate":
            raise SourcePrepareError("conform failed: unsupported codec")
        return SourcePreparationReport(source_plan=source_plan, records=())

    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        source_preparer=prepare,
        slate_plan=_realistic_slate_plan,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # FALLBACK_SLATE (111), a 120s plan
    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")

    started[0].returncode = 0  # the 120s slate reached EOS
    clock[0] += 120.0
    daemon.process_once("gov")  # one automatic relaunch -> program prepare fails -> slate 222
    state = store.read_state("gov")
    assert state.state == "FALLBACK_SLATE"
    assert state.pid == 222
    # (_start's prepare-failure fallback airs the slate plan as-is; the
    # slate is not re-prepared on that branch.)
    assert prepares == ["Fallback slate", "Council meeting"]

    started[1].returncode = 0  # 120s later the renewed slate reaches EOS again
    clock[0] += 120.0
    daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "STOPPED"
    assert "stopped instead of looping" in (state.last_error or "")
    assert "Check the program's media" in (state.last_error or "")
    assert [p.pid for p in started] == [111, 222]

    # And it STAYS stopped: no flap, no further plan resolves or prepares.
    for _ in range(3):
        clock[0] += 120.0
        daemon.process_once("gov")
    assert store.read_state("gov").state == "STOPPED"
    assert [p.pid for p in started] == [111, 222]
    assert prepares == ["Fallback slate", "Council meeting"]


def test_a_program_that_holds_healthy_air_resets_the_slate_eos_relaunch_count(
    tmp_path: Path,
) -> None:
    """Item 2: the counter clears once a REAL program airing (ON_AIR, not the
    slate) holds observed on-air evidence for the healthy-uptime window, so
    a later boundary gets its own relaunch again. A FALLBACK_SLATE worker
    holding the same uptime must NOT clear it (that is the flap the cap
    bounds)."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=pid) for pid in (111, 222, 333, 444)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # FALLBACK_SLATE (111)

    # The slate itself holds healthy uptime: streak bookkeeping may reset,
    # the slate-EOS counter must not (it is 0 here anyway; pin the rule with a
    # seeded count).
    daemon._slate_eos_relaunches["gov"] = 1  # type: ignore[attr-defined]
    log_path = daemon._stderr_logs["gov"]  # type: ignore[attr-defined]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("frame=100 fps=30.0 bitrate=6000.0kbits/s\n", encoding="utf-8")
    clock[0] += 1.0
    daemon.process_once("gov")  # evidence latched
    clock[0] += 65.0
    daemon.process_once("gov")  # healthy slate uptime
    assert store.read_state("gov").state == "FALLBACK_SLATE"
    assert daemon._slate_eos_relaunches.get("gov") == 1  # type: ignore[attr-defined]
    daemon._slate_eos_relaunches.pop("gov")  # type: ignore[attr-defined]

    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")
    started[0].returncode = 0
    daemon.process_once("gov")  # relaunch #1 -> ON_AIR (222), count 1
    assert store.read_state("gov").state == "ON_AIR"
    assert daemon._slate_eos_relaunches.get("gov") == 1  # type: ignore[attr-defined]

    # The program holds real on-air evidence for the healthy window (appended
    # past THIS worker's spawn offset -- the evidence gate is anchored there).
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("frame=200 fps=30.0 bitrate=6000.0kbits/s\n")
    clock[0] += 1.0
    daemon.process_once("gov")
    clock[0] += 65.0
    daemon.process_once("gov")
    assert store.read_state("gov").state == "ON_AIR"
    assert "gov" not in daemon._slate_eos_relaunches  # type: ignore[attr-defined]

    # The program ends cleanly -> STOPPED as before; an operator start with
    # nothing due lands on slate; that slate's EOS gets its own relaunch.
    store.enqueue_command(_command("stop").model_copy(update={"command_id": "cmd-stop-program"}))
    started[1].returncode = 0
    program_plan[0] = None
    daemon.process_once("gov")
    assert store.read_state("gov").state == "STOPPED"
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-restart",
        )
    )
    daemon.process_once("gov")  # FALLBACK_SLATE (333)
    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")
    started[2].returncode = 0
    daemon.process_once("gov")
    assert store.read_state("gov").state == "ON_AIR"
    assert store.read_state("gov").pid == 444


def test_slate_eos_after_a_crash_looped_live_source_renews_the_slate_not_the_source(
    tmp_path: Path,
) -> None:
    """Item 1 (HIGH). A live source that crash-looped past
    ``_LIVE_SOURCE_FAILURE_FALLBACK_STREAK`` parked the channel on the slate
    (streak stays at the threshold, latched). When that finite slate reaches
    EOS the relaunch must NOT go straight back onto the dead source (the
    reviewer's probe: state ON_AIR pid 106 "Live: Council chamber", streak
    still 5): it renews the slate through ``force_fallback_slate``, and a
    slate renewal does not count toward the relaunch cap (it is the B1
    terminal state that replaces dead air)."""
    from civiccast.egress.daemon import _LIVE_SOURCE_FAILURE_FALLBACK_STREAK

    n = _LIVE_SOURCE_FAILURE_FALLBACK_STREAK
    program_plan: list[EgressSourcePlan | None] = [_live_source_plan(tmp_path)]
    processes = [_FakeProcess(pid=100 + i) for i in range(n + 4)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    program_resolves = [0]
    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        program_resolves=program_resolves,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # ON_AIR on the live source (100)
    for _ in range(n):
        started[-1].returncode = 1  # the live source drops every time
        daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "FALLBACK_SLATE"
    assert daemon._restart_streak.get("gov") == n  # type: ignore[attr-defined]
    slate_pid = state.pid

    program_resolves[0] = 0
    started[-1].returncode = 0  # the finite slate reaches EOS
    clock[0] += 120.0
    daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "FALLBACK_SLATE"  # renewed, not the dead source
    assert state.current_source_label == "Fallback slate"
    assert state.pid == slate_pid + 1
    assert "renewed rather than retrying the failed source" in (state.last_error or "")
    assert program_resolves[0] == 0  # the dead source was not even asked for
    assert daemon._restart_streak.get("gov") == n  # type: ignore[attr-defined]
    assert "gov" not in daemon._slate_eos_relaunches  # type: ignore[attr-defined]

    # Renewals keep the station on slate for as long as the source stays dead.
    started[-1].returncode = 0
    clock[0] += 120.0
    daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "FALLBACK_SLATE"
    assert state.pid == slate_pid + 2


def test_slate_eos_inside_the_restart_cooldown_defers_through_the_backoff_path(
    tmp_path: Path,
) -> None:
    """Item 1 (HIGH), the latch half: the relaunch honours
    ``_restart_latch.should_run_now`` like ``_relaunch_after_crash`` does. A
    slate that ends inside the cooldown a crash-relaunch just armed is
    deferred (STARTING, ``_backoff_relaunch`` armed, no new worker) and
    fires from ``_service_backoff_relaunch`` once the latch permits."""
    program_plan: list[EgressSourcePlan | None] = [
        _source_plan_with_label(tmp_path, "Council meeting")
    ]
    processes = [_FakeProcess(pid=pid) for pid in (111, 222, 333)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        restart_cooldown_seconds=15.0,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # ON_AIR (111)
    started[0].returncode = 1
    daemon.process_once("gov")  # crash 1 -> immediate relaunch (222), latch armed 15s
    assert store.read_state("gov").pid == 222
    # Park the relaunched worker on the slate (as a source that fell back
    # would be) so its clean exit is a slate EOS.
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="Fallback slate",
            current_proof_event_id=None,
            updated_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
            pid=222,
        )
    )

    started[1].returncode = 0  # slate EOS 5s after the crash relaunch
    clock[0] += 5.0
    daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "STARTING"
    assert state.pid is None
    assert "inside the crash-relaunch cooldown" in (state.last_error or "")
    assert "gov" in daemon._backoff_relaunch  # type: ignore[attr-defined]
    assert [p.pid for p in started] == [111, 222]  # nothing spawned yet
    assert store.recent_health("gov", 1)[0].state == "STARTING"

    clock[0] += 15.0  # past the cooldown: the deferred relaunch fires
    daemon.process_once("gov")
    state = store.read_state("gov")
    assert state.state == "ON_AIR"
    assert state.pid == 333
    assert "gov" not in daemon._backoff_relaunch  # type: ignore[attr-defined]


@pytest.mark.parametrize("action", ["stop", "drain"])
def test_backoff_relaunch_with_a_queued_operator_stop_does_not_relaunch(
    tmp_path: Path, action: str
) -> None:
    """#212 review MINOR-1. A stop/drain queued DURING the cooldown: the tick
    that opens the latch runs ``_service_backoff_relaunch`` before it drains
    commands, so the deferred relaunch must peek the queue and step aside
    (no worker spawned) and the stop is honoured (STOPPED, no ``_start``)."""
    program_plan: list[EgressSourcePlan | None] = [
        _source_plan_with_label(tmp_path, "Council meeting")
    ]
    processes = [_FakeProcess(pid=pid) for pid in (111, 222, 333)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        restart_cooldown_seconds=15.0,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # ON_AIR (111)
    started[0].returncode = 1
    daemon.process_once("gov")  # crash 1 -> immediate relaunch (222), latch armed 15s
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="Fallback slate",
            current_proof_event_id=None,
            updated_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
            pid=222,
        )
    )
    started[1].returncode = 0  # slate EOS inside the cooldown -> deferred
    clock[0] += 5.0
    daemon.process_once("gov")
    assert store.read_state("gov").state == "STARTING"
    assert "gov" in daemon._backoff_relaunch  # type: ignore[attr-defined]

    store.enqueue_command(_command(action))  # queued during the cooldown
    clock[0] += 15.0  # the latch opens on this tick
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state.state == "STOPPED"
    assert [p.pid for p in started] == [111, 222]  # no worker the stop had to kill
    assert "gov" not in daemon._backoff_relaunch  # type: ignore[attr-defined]
    assert store.peek_pending_commands("gov") == []  # the command was drained


@pytest.mark.parametrize("action", ["stop", "drain"])
def test_slate_eos_with_a_queued_operator_stop_does_not_relaunch(
    tmp_path: Path, action: str
) -> None:
    """Item 3 (MEDIUM). ``process_once`` polls BEFORE it drains commands; a
    Stop queued while the slate was ending used to lose the race -- the
    relaunch spawned a worker (pids [111, 222] in the reviewer's probe) that
    the Stop then killed, after waiting behind the relaunch's cold prepare.
    The relaunch now peeks the queue and steps aside for a stop or drain."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    prepares: list[str] = []

    def prepare(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepares.append(source_plan.segments[0].label)
        return SourcePreparationReport(source_plan=source_plan, records=())

    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        source_preparer=prepare,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # FALLBACK_SLATE (111)
    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")

    store.enqueue_command(_command(action))  # queued, not yet drained
    started[0].returncode = 0  # the slate reaches EOS in the same tick
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state.state == "STOPPED"
    assert state.last_error is None
    assert [p.pid for p in started] == [111]  # no worker the stop had to kill
    assert prepares == ["Fallback slate"]  # no cold prepare ahead of the stop
    assert store.peek_pending_commands("gov") == []  # the command was drained


def test_stop_and_reset_restart_tracking_clear_the_slate_eos_relaunch_count(
    tmp_path: Path,
) -> None:
    """Item 4 (MEDIUM). The count must not outlive an operator stop or any
    other route through ``_reset_restart_tracking`` (clean exit, terminal
    error, healthy ON_AIR uptime)."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=111), _FakeProcess(pid=222)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # FALLBACK_SLATE (111)
    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")
    started[0].returncode = 0
    daemon.process_once("gov")  # relaunch #1 -> ON_AIR (222)
    assert daemon._slate_eos_relaunches.get("gov") == 1  # type: ignore[attr-defined]

    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")  # _stop -> _reset_restart_tracking
    assert store.read_state("gov").state == "STOPPED"
    assert "gov" not in daemon._slate_eos_relaunches  # type: ignore[attr-defined]

    daemon._slate_eos_relaunches["gov"] = 1  # type: ignore[attr-defined]
    daemon._reset_restart_tracking("gov")  # type: ignore[attr-defined]
    assert "gov" not in daemon._slate_eos_relaunches  # type: ignore[attr-defined]

    # The healthy-uptime caller keeps it while the channel is on the slate.
    daemon._slate_eos_relaunches["gov"] = 1  # type: ignore[attr-defined]
    daemon._reset_restart_tracking("gov", slate_eos_relaunches=False)  # type: ignore[attr-defined]
    assert daemon._slate_eos_relaunches.get("gov") == 1  # type: ignore[attr-defined]


def test_operator_start_clears_the_slate_eos_relaunch_count(tmp_path: Path) -> None:
    """Item 2/4: an operator start is a fresh intent -- the cap starts over
    even when the count was left behind by a relaunch whose program never
    held healthy air."""
    program_plan: list[EgressSourcePlan | None] = [None]
    processes = [_FakeProcess(pid=pid) for pid in (111, 222, 333)]
    started: list[_FakeProcess] = []
    clock = [1000.0]
    hook_calls: list[str] = []
    daemon = _slate_then_program_daemon(
        tmp_path,
        program_plan=program_plan,
        processes=processes,
        started=started,
        clock=clock,
        channel_start_hook=hook_calls.append,
    )
    store = daemon._store  # type: ignore[attr-defined]
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon.process_once("gov")  # FALLBACK_SLATE (111)
    program_plan[0] = _source_plan_with_label(tmp_path, "Council meeting")
    started[0].returncode = 0
    daemon.process_once("gov")  # relaunch #1 -> ON_AIR (222)
    assert daemon._slate_eos_relaunches.get("gov") == 1  # type: ignore[attr-defined]

    # Hook calls so far are the two GENUINE launch transitions (slate PID111,
    # then the program relaunch PID222).  That behaviour must be preserved; what
    # must not happen is the duplicate start below adding another call, because a
    # start on an already-live channel is a no-op that keeps the current writer
    # running -- session-scoped cleanup there would discard LIVE audio.
    assert hook_calls == ["gov", "gov"], (
        f"expected one hook call per genuine launch transition, got {hook_calls}"
    )
    genuine_calls = len(hook_calls)
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime(2026, 6, 5, 13, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-restart",
        )
    )
    daemon.process_once("gov")  # the worker is alive: start is a no-op ON_AIR rewrite
    assert store.read_state("gov").pid == 222
    assert "gov" not in daemon._slate_eos_relaunches  # type: ignore[attr-defined]
    assert len(hook_calls) == genuine_calls, (
        "the duplicate START on a live channel fired the session-start hook, "
        "which would run session-scoped cleanup against the live writer's audio"
    )


def test_peek_pending_commands_does_not_consume(tmp_path: Path) -> None:
    """The store seam item 3 relies on: peeking returns the same ordered
    commands ``pop_pending_commands`` will, without marking them consumed."""
    store = InMemoryEgressStore()
    later = EgressCommand(
        channel_id="gov",
        action="stop",
        issued_at=datetime(2026, 6, 5, 12, 1, tzinfo=UTC),
        issued_by="operator",
        command_id="cmd-later",
    )
    store.enqueue_command(later)
    store.enqueue_command(_command("start"))
    assert [c.command_id for c in store.peek_pending_commands("gov")] == ["cmd-start", "cmd-later"]
    assert [c.command_id for c in store.peek_pending_commands("gov")] == ["cmd-start", "cmd-later"]
    assert store.peek_pending_commands("other") == []
    assert [c.command_id for c in store.pop_pending_commands("gov")] == ["cmd-start", "cmd-later"]
    assert store.peek_pending_commands("gov") == []


# ---------------------------------------------------------------------------
# F3(b) (U14, owner-authorized 2026-09-24): a NON-deferred content reload issued
# while the channel is on FALLBACK_SLATE must not attempt the in-place selector
# swap -- that combination tore down the live slate leg under the selector and
# wedged one commit for 270 s on 2026-09-24 (U13). It takes the daemon's own
# terminate+restart path instead, carrying the plan the reload ALREADY prepared
# so the restart does not conform it a second time (measured 133.6 s for the
# 18:48 government program).
# ---------------------------------------------------------------------------


class _PlanLabelRecordingStrategy(_FakeContentReloadStrategy):
    """Records the label of every plan a ``start`` was handed, so a test can
    prove WHICH plan the restarted worker actually airs."""

    def __init__(self, processes: list[_FakeProcess], started: list[_FakeProcess]) -> None:
        super().__init__(processes, started)
        self.started_labels: list[str] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        self.started_labels.append(request.source_plan.segments[0].label)
        return super().start(request)


def _hold_fallback_slate_restart_plan(tmp_path: Path) -> SimpleNamespace:
    """Drive one FALLBACK_SLATE + immediate (non-deferred) program-due content
    reload up to the moment its prepared plan is HELD for the restart, and
    return that run's observation surface.

    By the time this returns the slate worker has been terminated (that is what
    triggers the restart) but the restart itself has NOT run: the next
    ``process_once`` tick is what consumes the held plan."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    released: list[Any] = []  # hook is Path | None
    started: list[_FakeProcess] = []
    prepared_labels: list[str] = []
    provider_calls: list[str] = []
    counter = {"n": 0}
    base_prepare = _prepare_with_tracked_plan_dirs(tmp_path, counter)
    old_process = _FakeProcess(pid=111)
    new_process = _FakeProcess(pid=222)
    strategy = _PlanLabelRecordingStrategy([new_process], started)

    def source_provider(channel_id: str) -> EgressSourcePlan:
        provider_calls.append(channel_id)
        return _source_plan_with_label(tmp_path, "Due program")

    def prepare(plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepared_labels.append(plan.segments[0].label)
        return base_prepare(plan, config)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=source_provider,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,
        source_preparer=prepare,
        prepared_plan_release=released.append,
    )
    daemon.enable_async_preparation()
    # A live worker already airing the fallback slate (no process_once start
    # needed -- _request_reload only reads state + the tracked process).
    daemon._processes["gov"] = old_process
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="CivicCast slate",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )

    daemon._request_reload("gov")
    assert daemon._preparations["gov"].kind == "reload"
    daemon._preparations["gov"].future.result(timeout=10)
    daemon.process_once("gov")  # completes the preparation: hold the plan + kill the slate worker

    return SimpleNamespace(
        daemon=daemon,
        store=store,
        strategy=strategy,
        released=released,
        started=started,
        prepared_labels=prepared_labels,
        provider_calls=provider_calls,
        old_process=old_process,
        new_process=new_process,
    )


def test_second_reload_after_dead_slate_worker_clears_old_restart_state(
    tmp_path: Path,
) -> None:
    """A second reload that starts past a dead slate worker cannot inherit
    the first reload's TRANSITIONING pin or deliberate-kill bookkeeping."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    started: list[_FakeProcess] = []
    old_process = _FakeProcess(pid=111)
    new_process = _FakeProcess(pid=222)
    strategy = _FakeContentReloadStrategy([new_process], started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,
    )
    daemon._processes["gov"] = old_process
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="Fallback slate",
            pid=old_process.pid,
            updated_at=datetime.now(UTC),
        )
    )

    store.enqueue_commands(
        [
            EgressCommand(
                channel_id="gov",
                action="reload",
                issued_at=datetime.now(UTC),
                issued_by="operator",
                command_id=command_id,
            )
            for command_id in ("reload-first", "reload-second")
        ]
    )
    assert daemon.process_once("gov") == 2
    assert old_process.terminated is True
    assert started == [new_process]

    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert daemon._pending_reloads == {}
    assert daemon._reload_kills == set()
    assert daemon._reload_kill_reasons == {}
    assert daemon._reload_stall_since == {}
    assert daemon._reload_stall_bound_s == {}
    assert daemon._reload_stall_rungs == {}


def test_fresh_start_releases_abandoned_plan_and_uses_supplied_plan(tmp_path: Path) -> None:
    """Fresh-start cleanup releases a stale held plan without releasing a
    distinct prepared plan explicitly handed to that start."""
    from civiccast.egress.daemon import _ReusePreparedPlan

    run = _hold_fallback_slate_restart_plan(tmp_path)
    supplied_dir = tmp_path / "supplied-plan"
    supplied_dir.mkdir()
    supplied = _ReusePreparedPlan(
        report=SourcePreparationReport(
            source_plan=_source_plan_with_label(tmp_path, "Supplied program"),
            records=(),
            plan_dir=supplied_dir,
        ),
        target_state="ON_AIR",
    )

    run.daemon._start("gov", prepared_reload=supplied)

    assert run.released == [tmp_path / "plan-1"]
    assert run.strategy.started_labels == ["Supplied program"]
    assert run.daemon._active_prepared_plan_dir["gov"] == supplied_dir
    assert run.daemon._prepared_restart_plans == {}


def test_live_worker_start_noop_preserves_reload_bookkeeping(tmp_path: Path) -> None:
    """A start that finds a live worker leaves its reload state to that worker."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    process = _FakeProcess(pid=111)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=_FakeContentReloadStrategy([_FakeProcess(pid=222)], []),
    )
    daemon._processes["gov"] = process
    daemon._pending_reloads["gov"] = ("FALLBACK_SLATE", "Fallback slate")
    daemon._reload_kills.add("gov")
    daemon._reload_kill_reasons["gov"] = "still belongs to the live worker"
    daemon._reload_stall_since["gov"] = 1.0
    daemon._reload_stall_bound_s["gov"] = 600.0
    daemon._reload_stall_rungs["gov"] = 1

    daemon._start("gov")

    assert daemon._processes["gov"] is process
    assert daemon._pending_reloads == {"gov": ("FALLBACK_SLATE", "Fallback slate")}
    assert daemon._reload_kills == {"gov"}
    assert daemon._reload_kill_reasons == {"gov": "still belongs to the live worker"}
    assert daemon._reload_stall_since == {"gov": 1.0}
    assert daemon._reload_stall_bound_s == {"gov": 600.0}
    assert daemon._reload_stall_rungs == {"gov": 1}


def test_fallback_slate_immediate_reload_restarts_onto_the_plan_it_prepared(
    tmp_path: Path,
) -> None:
    """The whole F3(b) contract, in one run: no seamless reload is requested,
    the worker is restarted, the preparer ran exactly ONCE for the reload, and
    that restart airs the report's own plan and directory."""
    run = _hold_fallback_slate_restart_plan(tmp_path)

    # Nothing was sent to the worker's in-place reload seam at all.
    assert run.strategy.reload_calls == []
    assert run.strategy.switch_at_end_of_current_calls == []
    # ... and the slate worker was terminated, which is the restart's trigger.
    assert run.old_process.terminated is True
    # Exactly one conform for this reload, and its directory is the one held --
    # live for the preparer's GC while the restart is in flight.
    assert run.prepared_labels == ["Due program"]
    assert run.provider_calls == ["gov"]
    assert run.released == []
    assert run.daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})

    run.daemon.process_once("gov")  # the pending-reload restart consumes it

    assert run.strategy.started_labels == ["Due program"]
    assert run.started == [run.new_process]
    assert run.daemon._processes["gov"] is run.new_process
    # The restarted start aired the prepared directory itself, and neither
    # re-prepared nor re-resolved the plan.
    assert run.daemon._active_prepared_plan_dir["gov"] == tmp_path / "plan-1"
    assert run.prepared_labels == ["Due program"]
    assert run.provider_calls == ["gov"]
    assert run.released == []
    # Consumed, so nothing is held any more.
    assert run.daemon._prepared_restart_plans == {}
    state = run.store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Due program"


def test_fallback_slate_immediate_reload_reuses_the_plan_on_the_synchronous_path(
    tmp_path: Path,
) -> None:
    """Same contract through the SYNCHRONOUS preparation path (no
    ``enable_async_preparation`` executor): the steps finish inline, so the
    decision reaches ``_try_content_reload`` directly. Without handling it
    there the carry would be dropped and the reload would silently never
    restart."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    released: list[Any] = []  # hook is Path | None
    started: list[_FakeProcess] = []
    prepared_labels: list[str] = []
    counter = {"n": 0}
    base_prepare = _prepare_with_tracked_plan_dirs(tmp_path, counter)
    old_process = _FakeProcess(pid=111)
    new_process = _FakeProcess(pid=222)
    strategy = _PlanLabelRecordingStrategy([new_process], started)

    def prepare(plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepared_labels.append(plan.segments[0].label)
        return base_prepare(plan, config)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Due program"),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,
        source_preparer=prepare,
        prepared_plan_release=released.append,
    )
    daemon._processes["gov"] = old_process
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="CivicCast slate",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )

    state = store.read_state("gov")
    assert state is not None
    applied = daemon._try_content_reload("gov", state, old_process, rollover_plan_end_at=None)

    assert applied is False
    assert strategy.reload_calls == []
    assert strategy.switch_at_end_of_current_calls == []
    assert prepared_labels == ["Due program"]
    assert old_process.terminated is True
    assert released == []
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})

    daemon.process_once("gov")

    assert strategy.started_labels == ["Due program"]
    assert prepared_labels == ["Due program"]
    assert daemon._active_prepared_plan_dir["gov"] == tmp_path / "plan-1"
    assert released == []


def test_fallback_slate_immediate_filler_rollover_comes_back_up_on_the_slate(
    tmp_path: Path,
) -> None:
    """A force-fallback filler rollover is the one route to a non-deferred
    reload whose prepared plan is the SLATE itself. The restart must publish
    FALLBACK_SLATE for it -- not ON_AIR over a slate plan."""
    released: list[Any] = []  # hook is Path | None
    started: list[_FakeProcess] = []
    prepared_labels: list[str] = []
    counter = {"n": 0}
    base_prepare = _prepare_with_tracked_plan_dirs(tmp_path, counter)
    old_process = _FakeProcess(pid=111)
    new_process = _FakeProcess(pid=222)
    strategy = _PlanLabelRecordingStrategy([new_process], started)

    def prepare(plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepared_labels.append(plan.segments[0].label)
        return base_prepare(plan, config)

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Live feed"),
        boundary_source_plan_provider=lambda _cid, _at: None,
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,
        source_preparer=prepare,
        prepared_plan_release=released.append,
    )
    daemon.enable_async_preparation()
    daemon._processes["gov"] = old_process
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="CivicCast slate",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )
    # A horizon that has already passed is what makes should_defer_switch cut
    # immediately even for a filler rollover.
    command_id = "rollover-filler"
    daemon.record_rollover_plan_end(
        "gov",
        datetime.now(UTC) - timedelta(minutes=5),
        command_id=command_id,
        force_fallback=True,
    )
    daemon._request_reload("gov", command_id=command_id)
    assert daemon._preparations["gov"].kind == "reload"
    daemon._preparations["gov"].future.result(timeout=10)
    daemon.process_once("gov")

    assert strategy.reload_calls == []
    assert prepared_labels == ["Fallback slate"]
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})

    daemon.process_once("gov")

    assert strategy.started_labels == ["Fallback slate"]
    assert prepared_labels == ["Fallback slate"]
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "FALLBACK_SLATE"
    assert state.current_source_label == "Fallback slate"


def test_on_air_deferred_rollover_still_arms_the_seamless_swap_with_a_preparer(
    tmp_path: Path,
) -> None:
    """F3(b) is scoped to FALLBACK_SLATE + a NON-deferred switch. An ON_AIR
    rollover that defers to the outgoing leg's own EOS must keep the in-place
    selector swap exactly as it was -- even with a preparer configured, and
    even though that prepare completes synchronously here."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    released: list[Any] = []  # hook is Path | None
    started: list[_FakeProcess] = []
    prepared_labels: list[str] = []
    counter = {"n": 0}
    base_prepare = _prepare_with_tracked_plan_dirs(tmp_path, counter)
    live_process = _FakeProcess(pid=111)
    strategy = _PlanLabelRecordingStrategy([_FakeProcess(pid=222)], started)

    def prepare(plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepared_labels.append(plan.segments[0].label)
        return base_prepare(plan, config)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: _source_plan_with_label(tmp_path, "Live feed"),
        boundary_source_plan_provider=lambda _cid, _at: _source_plan_with_label(
            tmp_path, "Due program"
        ),
        encoder_strategy=strategy,
        source_preparer=prepare,
        prepared_plan_release=released.append,
    )
    daemon._processes["gov"] = live_process
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Live feed",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )
    command_id = "rollover-on-air"
    daemon.record_rollover_plan_end(
        "gov", datetime.now(UTC) + timedelta(minutes=20), command_id=command_id
    )
    daemon._request_reload("gov", command_id=command_id)

    # Deferred: the swap is armed against the outgoing leg's own EOS.
    assert strategy.reload_calls == ["Due program"]
    assert strategy.switch_at_end_of_current_calls == [True]
    # Nothing was terminated, nothing was restarted, nothing was released.
    assert live_process.terminated is False
    assert daemon._processes["gov"] is live_process
    assert started == []
    assert prepared_labels == ["Due program"]
    assert released == []
    # The seamless path's own tracking holds the prepared plan while it settles.
    assert daemon.live_prepared_plan_dirs("gov") == frozenset({tmp_path / "plan-1"})
    assert daemon._prepared_restart_plans == {}


@pytest.mark.parametrize("tracked_process", [True, False])
def test_held_prepared_restart_plan_is_released_on_operator_stop(
    tmp_path: Path, tracked_process: bool
) -> None:
    run = _hold_fallback_slate_restart_plan(tmp_path)
    if not tracked_process:
        run.daemon._processes.pop("gov", None)

    run.daemon._stop("gov", draining=False)

    assert run.released == [tmp_path / "plan-1"]
    assert run.daemon._prepared_restart_plans == {}
    assert run.daemon.live_prepared_plan_dirs("gov") == frozenset()


@pytest.mark.parametrize("tracked_process", [True, False])
def test_held_prepared_restart_plan_is_released_on_drain(
    tmp_path: Path, tracked_process: bool
) -> None:
    run = _hold_fallback_slate_restart_plan(tmp_path)
    if not tracked_process:
        run.daemon._processes.pop("gov", None)

    run.daemon._drain("gov")

    assert run.released == [tmp_path / "plan-1"]
    assert run.daemon._prepared_restart_plans == {}
    assert run.daemon.live_prepared_plan_dirs("gov") == frozenset()


@pytest.mark.parametrize("tracked_process", [True, False])
def test_held_prepared_restart_plan_is_released_on_service_shutdown(
    tmp_path: Path, tracked_process: bool
) -> None:
    run = _hold_fallback_slate_restart_plan(tmp_path)
    if not tracked_process:
        run.daemon._processes.pop("gov", None)

    run.daemon.stop_all_channels(deadline_seconds=0.0)

    assert run.released == [tmp_path / "plan-1"]
    assert run.daemon._prepared_restart_plans == {}
    assert run.daemon.live_prepared_plan_dirs("gov") == frozenset()


@pytest.mark.parametrize("settle_fresh_handoff", [False, True])
def test_held_prepared_restart_plan_is_released_when_the_exit_takes_no_pending_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settle_fresh_handoff: bool
) -> None:
    """A worker exit that does NOT take the pending-reload restart (here: it
    crashes instead of being the deliberate reload kill) can never air the held
    plan, so it must be released. Ordinary crash recovery may restart on slate,
    but must not restart onto that unrelated held plan."""
    run = _hold_fallback_slate_restart_plan(tmp_path)
    abandoned = run.daemon._prepared_restart_plans["gov"]
    # The exit arrives as a crash with no pending reload: drop both pieces of
    # bookkeeping the deliberate-kill path had set.
    run.daemon._pending_reloads.pop("gov", None)
    run.daemon._reload_kills.discard("gov")
    run.old_process.returncode = 1

    poll_preparation = run.daemon._poll_preparation
    fresh_preparations = []

    def control_fresh_handoff(channel_id: str) -> None:
        # Crash recovery airs slate first and queues a NEW program handoff.
        # Either scheduling outcome is legitimate; do not rely on how quickly
        # the executor finishes under instrumentation or CPU contention.
        pending = run.daemon._preparations[channel_id]
        report = pending.future.result(timeout=10)
        fresh_preparations.append((pending, report))
        if settle_fresh_handoff:
            poll_preparation(channel_id)

    monkeypatch.setattr(run.daemon, "_poll_preparation", control_fresh_handoff)
    run.daemon.process_once("gov")

    # Assert outside the daemon's exception-isolating poll boundary.
    assert len(fresh_preparations) == 1
    pending, report = fresh_preparations[0]
    assert pending.kind == "reload"
    assert pending.slate_first is True
    assert pending.process is run.new_process
    assert report is not abandoned.report
    assert report.plan_dir == tmp_path / "plan-2"
    assert report.source_plan.segments[0].label == "Due program"
    assert run.released == [tmp_path / "plan-1"]
    assert tmp_path / "plan-1" not in run.daemon.live_prepared_plan_dirs("gov")
    if settle_fresh_handoff:
        fresh = run.daemon._prepared_restart_plans["gov"]
        assert fresh is not abandoned
        assert fresh.report.plan_dir == tmp_path / "plan-2"
        assert fresh.target_state == "ON_AIR"
        assert set(run.daemon._prepared_restart_plans) == {"gov"}
    else:
        assert run.daemon._prepared_restart_plans == {}
    assert run.prepared_labels == ["Due program", "Due program"]
    assert run.provider_calls == ["gov", "gov"]
    assert run.strategy.started_labels == ["Fallback slate"]
    assert run.started == [run.new_process]


def test_held_prepared_restart_plan_is_released_when_a_newer_one_supersedes_it(
    tmp_path: Path,
) -> None:
    """Two prepared restarts for one channel must not both hold a directory:
    the superseded plan is released, the newer one is held."""
    from civiccast.egress.daemon import _ReusePreparedPlan

    run = _hold_fallback_slate_restart_plan(tmp_path)
    newer_dir = tmp_path / "plan-2"
    newer_dir.mkdir()
    newer = _ReusePreparedPlan(
        report=SourcePreparationReport(
            source_plan=_source_plan_with_label(tmp_path, "Newer"), records=(), plan_dir=newer_dir
        ),
        target_state="ON_AIR",
    )

    run.daemon._stash_prepared_restart_plan("gov", newer)

    assert run.released == [tmp_path / "plan-1"]
    assert run.daemon.live_prepared_plan_dirs("gov") == frozenset({newer_dir})


# ---------------------------------------------------------------------------
# U26 defect 2: a reload that resolves the schedule at wall-clock NOW must not
# land the channel on the closing seconds of the item that is due.
#
# Live evidence (government, 2026-09-25 02:58 MDT): the slate -> program reload
# prepared ONE segment covering only the remainder of the item whose slot was
# closing. The engine reached EOS at 02:58:19,385 while that plan's own end --
# the daemon's stale-horizon WARNING at 02:58:31.968 quotes it -- was
# 2026-09-25T08:58:30.392844+00:00, ~11s later. The restart went ON_AIR at
# 02:58:26,207, aired the stub to EOS at 02:58:34,137, and the channel needed a
# SECOND worker start, reaching ON_AIR at 02:58:36,574. Two worker starts and
# ~2.4s of dead air to reach a program that was due the whole time.
#
# The tail is structural, not a one-off: with the GStreamer engine selected the
# production provider is built with max_segments=1 (automation.py:2634), so the
# plan for a closing slot is always exactly that slot's remainder.
# ---------------------------------------------------------------------------


def _plan_with_seconds(tmp_path: Path, label: str, seconds: float) -> EgressSourcePlan:
    source = tmp_path / f"{label.replace(' ', '-').replace(':', '').lower()}.ts"
    source.write_text(label, encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label=label,
                path=str(source),
                duration_seconds=seconds,
                source_ref=f"asset-{label}",
            )
        ],
    )


def _tail_guard_daemon(
    tmp_path: Path,
    boundary: Callable[[str, datetime], EgressSourcePlan | None],
) -> EgressDaemon:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    return EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        boundary_source_plan_provider=boundary,
    )


def test_reload_onto_a_degenerate_schedule_tail_prepares_the_next_item(
    tmp_path: Path,
) -> None:
    """U26 defect 2, the live case: a slate -> program reload resolves the item
    whose slot is closing (9.2s left in this fixture; ~11s in the live log) and
    restarts the worker onto it. The restart path itself (F3(b)) is untouched --
    but the plan it carries must be the item due where that tail ENDS, so the
    one restart the channel pays for airs the program the automation asked for
    instead of the closing item's remainder."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    started: list[_FakeProcess] = []
    prepared_labels: list[str] = []
    boundary_args: list[datetime] = []
    counter = {"n": 0}
    base_prepare = _prepare_with_tracked_plan_dirs(tmp_path, counter)
    old_process = _FakeProcess(pid=111)
    strategy = _PlanLabelRecordingStrategy([_FakeProcess(pid=222)], started)

    def boundary(channel_id: str, at: datetime) -> EgressSourcePlan:
        boundary_args.append(at)
        return _plan_with_seconds(tmp_path, "City Council", 1800.0)

    def prepare(plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        prepared_labels.append(plan.segments[0].label)
        return base_prepare(plan, config)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _plan_with_seconds(
            tmp_path, "Longmont Weather :16", 9.2
        ),
        boundary_source_plan_provider=boundary,
        encoder_strategy=strategy,
        source_preparer=prepare,
    )
    daemon.enable_async_preparation()
    daemon._processes["gov"] = old_process
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="CivicCast slate",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )
    before = datetime.now(UTC)
    daemon._request_reload("gov")
    assert daemon._preparations["gov"].kind == "reload"
    daemon._preparations["gov"].future.result(timeout=10)

    daemon.process_once("gov")

    # The degenerate tail never even reaches the preparer.
    assert prepared_labels == ["City Council"]
    assert len(boundary_args) == 1
    # Resolved at the tail's own end plus the guard's margin -- strictly past
    # the tail, which is the whole point: the NEXT item is what is due there.
    assert boundary_args[0] >= before + timedelta(seconds=10.2)
    assert boundary_args[0] <= before + timedelta(seconds=20.0)
    # F3(b) unchanged: still the restart path, not an in-place selector swap.
    assert strategy.reload_calls == []
    assert old_process.terminated is True

    daemon.process_once("gov")

    assert strategy.started_labels == ["City Council"]


def test_reload_onto_a_healthy_schedule_tail_never_consults_the_boundary(
    tmp_path: Path,
) -> None:
    """The guard must be invisible whenever the tail is worth airing: no extra
    provider call, and the ordinary ON_AIR in-place reload exactly as it was."""
    boundary_calls: list[datetime] = []

    def boundary(channel_id: str, at: datetime) -> EgressSourcePlan:
        boundary_calls.append(at)
        return _plan_with_seconds(tmp_path, "Some other item", 1800.0)

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    started: list[_FakeProcess] = []
    strategy = _PlanLabelRecordingStrategy([_FakeProcess(pid=222)], started)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _plan_with_seconds(
            tmp_path, "City Council", 1800.0
        ),
        boundary_source_plan_provider=boundary,
        encoder_strategy=strategy,
    )
    process = _FakeProcess(pid=111)
    daemon._processes["gov"] = process
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Longmont Weather :16",
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )

    daemon._request_reload("gov")

    assert boundary_calls == []
    assert strategy.reload_calls == ["City Council"]
    assert strategy.switch_at_end_of_current_calls == [True]
    assert process.terminated is False


@pytest.mark.parametrize("tail_seconds", [29.9, 0.5])
def test_degenerate_tail_guard_is_consulted_below_the_floor(
    tmp_path: Path, tail_seconds: float
) -> None:
    """N = 30s: below it the boundary is asked for and its answer is used."""
    seen: list[datetime] = []

    def boundary(channel_id: str, at: datetime) -> EgressSourcePlan:
        seen.append(at)
        return _plan_with_seconds(tmp_path, "City Council", 1800.0)

    daemon = _tail_guard_daemon(tmp_path, boundary)
    tail = _plan_with_seconds(tmp_path, "Longmont Weather :16", tail_seconds)

    resolved = daemon._avoid_schedule_tail_plan("gov", tail)

    assert resolved.segments[0].label == "City Council"
    assert len(seen) == 1


@pytest.mark.parametrize("tail_seconds", [30.0, 1800.0])
def test_degenerate_tail_guard_is_inert_at_or_above_the_floor(
    tmp_path: Path, tail_seconds: float
) -> None:
    """At the floor and above, the plan is returned untouched and no provider
    call is made -- 30s of real program is worth airing on its own."""
    seen: list[datetime] = []

    def boundary(channel_id: str, at: datetime) -> EgressSourcePlan:
        seen.append(at)
        return _plan_with_seconds(tmp_path, "City Council", 1800.0)

    daemon = _tail_guard_daemon(tmp_path, boundary)
    tail = _plan_with_seconds(tmp_path, "Longmont Weather :16", tail_seconds)

    assert daemon._avoid_schedule_tail_plan("gov", tail) is tail
    assert seen == []


def test_degenerate_tail_guard_keeps_the_tail_when_the_boundary_is_not_better(
    tmp_path: Path,
) -> None:
    """Every way the boundary can fail to improve on the tail keeps today's
    behavior: air the tail. A media-shorter-than-slot tail, a gap the fill
    policy owns, the end of the schedule, a channel id that is not this
    channel's, or a preparation error -- none of them may leave the channel
    with a plan it did not have before."""

    def _raise(_channel_id: str, _at: datetime) -> EgressSourcePlan | None:
        raise SourcePrepareError("scheduled asset is not playable")

    boundaries: list[Callable[[str, datetime], EgressSourcePlan | None]] = [
        lambda _channel_id, _at: None,
        lambda _channel_id, _at: _plan_with_seconds(tmp_path, "Shorter", 5.0),
        lambda _channel_id, _at: _plan_with_seconds(tmp_path, "Same length", 9.2),
        lambda _channel_id, _at: _plan_with_seconds(tmp_path, "Other channel", 900.0).model_copy(
            update={"channel_id": "clr"}
        ),
        _raise,
    ]
    tail = _plan_with_seconds(tmp_path, "Longmont Weather :16", 9.2)

    for boundary in boundaries:
        daemon = _tail_guard_daemon(tmp_path, boundary)
        assert daemon._avoid_schedule_tail_plan("gov", tail) is tail


def test_short_known_programme_is_not_replaced_by_slate_at_the_tail_floor(
    tmp_path: Path,
) -> None:
    """An intentional 20s clip remains airable with 16.9s left.

    The remaining plan duration alone must not make a short Welcome clip look
    like the dying tail of a long programme.
    """
    store = InMemoryEgressStore()
    config = _config()
    store.upsert_config(config)
    boundary_calls: list[datetime] = []

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan | None:
        boundary_calls.append(at)
        return None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        boundary_source_plan_provider=boundary,
        fallback_source_provider=lambda _config: _plan_with_seconds(
            tmp_path, "Fallback slate", 3600.0
        ),
    )
    clip = _plan_with_seconds(tmp_path, "Welcome", 16.9)
    clip_segment = clip.segments[0].model_copy(
        update={
            "inpoint_seconds": 3.1,
            "playable_duration_seconds": 20.0,
        }
    )
    clip = clip.model_copy(update={"segments": [clip_segment]})

    outcome = daemon._resolve_schedule_tail(
        "gov",
        clip,
        config,
        resolved_at=datetime(2026, 10, 12, 22, 33, tzinfo=UTC),
        slate_when_no_next=True,
    )

    assert outcome.plan is clip
    assert outcome.slate is False
    assert boundary_calls == []


def test_known_long_programme_tail_still_uses_slate_without_a_next_item(
    tmp_path: Path,
) -> None:
    """Known long media near EOS remains protected by the 30s tail guard."""
    store = InMemoryEgressStore()
    config = _config()
    store.upsert_config(config)
    boundary_calls: list[datetime] = []

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan | None:
        boundary_calls.append(at)
        return None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        boundary_source_plan_provider=boundary,
        fallback_source_provider=lambda _config: _plan_with_seconds(
            tmp_path, "Fallback slate", 3600.0
        ),
    )
    long_tail = _plan_with_seconds(tmp_path, "Council meeting", 16.9)
    tail_segment = long_tail.segments[0].model_copy(
        update={
            "inpoint_seconds": 3590.0,
            "playable_duration_seconds": 3600.0,
        }
    )
    long_tail = long_tail.model_copy(update={"segments": [tail_segment]})

    outcome = daemon._resolve_schedule_tail(
        "gov",
        long_tail,
        config,
        resolved_at=datetime(2026, 10, 12, 22, 33, tzinfo=UTC),
        slate_when_no_next=True,
    )

    assert outcome.plan.segments[0].label == "Fallback slate"
    assert outcome.slate is True
    assert len(boundary_calls) == 1


def test_degenerate_tail_guard_is_inert_without_a_boundary_provider(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
    )
    tail = _plan_with_seconds(tmp_path, "Longmont Weather :16", 9.2)

    assert daemon._avoid_schedule_tail_plan("gov", tail) is tail


def test_degenerate_tail_guard_is_inert_during_a_manual_override(
    tmp_path: Path,
) -> None:
    """An operator override owns the plan resolution: the override's own
    contracts resolve at wall-clock now, so the guard must not reach for a
    later boundary behind it."""
    seen: list[datetime] = []

    def boundary(channel_id: str, at: datetime) -> EgressSourcePlan:
        seen.append(at)
        return _plan_with_seconds(tmp_path, "City Council", 1800.0)

    class _OverrideDaemon(EgressDaemon):
        def has_manual_override(self, channel_id: str) -> bool:
            return True

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = _OverrideDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        boundary_source_plan_provider=boundary,
    )
    tail = _plan_with_seconds(tmp_path, "Longmont Weather :16", 9.2)

    assert daemon._avoid_schedule_tail_plan("gov", tail) is tail
    assert seen == []


# ---------------------------------------------------------------------------
# U36 (2026-09-25): the schedule tail floor on the horizon-bound rollover and
# on the relaunch path, plus the bounded retry for an aborted boundary reload.
#
# Defect: a program -> program rollover switched onto a ~2s sliver of the
# program that was already ending; the new leg EOSed immediately (with the
# GStreamer engine max_segments=1 makes that leg the WHOLE pipeline) and the
# worker exited. Measured on the public channel 2026-09-25: the 14:06:50
# rollover resolved the July 23 program AT ITS HORIZON with the drifted
# schedule clock (664s elapsed of a 667s item) and its new leg delivered 1.6s
# (``mux-in 1.6s: video=+24 audio=+37``) before EOS. The horizon is the
# OUTGOING leg's end, but the schedule clock and that leg's real start
# disagree -- which is exactly why "the boundary is the tail's own end" (the
# U26 reasoning that exempted this path) does not hold.
# ---------------------------------------------------------------------------


class _AbortingReloadStrategy(_FakeContentReloadStrategy):
    """A content-reload strategy whose worker ABORTS the reload pre-commit
    (``CTRL reload aborted: new program errored before commit``), the way the
    2026-09-25 public channel's boundary reloads did. Arms normally but
    settles ``abort_results`` in order (any further arming settles
    ``applied``)."""

    def __init__(
        self,
        processes: list[_FakeProcess],
        started: list[_FakeProcess],
        *,
        abort_results: list[str] | None = None,
    ) -> None:
        super().__init__(processes, started, auto_settle=False)
        self._abort_results = list(abort_results or [])

    def reload_content(
        self,
        channel_id: str,
        work_dir: Path,
        request: EncoderStartRequest,
        *,
        command_id: str | None = None,
    ) -> bool:
        accepted = super().reload_content(channel_id, work_dir, request, command_id=command_id)
        result = self._abort_results.pop(0) if self._abort_results else "applied"
        _write_fake_reload_status(work_dir, channel_id, command_id, result)
        return accepted


def _u36_rollover_daemon(
    tmp_path: Path,
    *,
    strategy: Any,
    boundary: Callable[[str, datetime], EgressSourcePlan | None],
    fallback: Callable[[EgressConfig], EgressSourcePlan] | None = None,
) -> EgressDaemon:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    return EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _plan_with_seconds(tmp_path, "Unused", 600.0),
        boundary_source_plan_provider=boundary,
        fallback_source_provider=fallback,
        encoder_strategy=strategy,
    )


def _u36_on_air(
    daemon: EgressDaemon, *, pid: int = 111, label: str = "Longmont Weather :16"
) -> _FakeProcess:
    process = _FakeProcess(pid=pid)
    daemon._processes["gov"] = process
    daemon._store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label=label,
            updated_at=datetime(2026, 6, 12, 6, 0, tzinfo=UTC),
        )
    )
    return process


@pytest.mark.parametrize(
    ("scenario", "expected_label", "expected_state"),
    [
        ("no_next", "CivicCast slate", "FALLBACK_SLATE"),
        ("next_program", "City Council", "ON_AIR"),
        ("same_asset_new_occurrence", "Welcome", "ON_AIR"),
    ],
)
def test_scheduled_short_program_remainder_rolls_to_slate_or_next_program(
    tmp_path: Path,
    scenario: str,
    expected_label: str,
    expected_state: str,
) -> None:
    """A real schedule lookup can return the same short media later in its slot.

    Keep the intentional 20s Welcome airing at the slot start. A later lookup
    of that occurrence with an advanced join-in-progress offset must resolve
    past its media end; a separately scheduled use of the same asset stays a
    fresh short programme.
    """
    strategy = _FakeContentReloadStrategy([], [], auto_settle=False)
    boundary_calls: list[datetime] = []
    boundary_plans: list[EgressSourcePlan | None] = []
    first_horizon = datetime.now(UTC) + timedelta(seconds=60.0)
    second_horizon = first_horizon + timedelta(
        seconds=75.0 if scenario == "same_asset_new_occurrence" else 15.0
    )
    welcome_path = tmp_path / "welcome.ts"
    welcome_path.write_text("fake welcome media", encoding="utf-8")
    items = [
        ScheduleItemResponse(
            id=uuid.uuid4(),
            asset_id="welcome-asset",
            asset_title="Welcome",
            channel_id="gov",
            mode="premiere",
            state="published",
            scheduled_at=first_horizon,
            duration_seconds=60,
            notes=None,
            created_at=first_horizon - timedelta(days=1),
        )
    ]
    assets = {
        "welcome-asset": StaffAssetRow(
            asset_id="welcome-asset",
            title="Welcome",
            state="validated",
            file_path=str(welcome_path),
            duration_seconds=20,
            trim_in_seconds=None,
            trim_out_seconds=20,
        )
    }
    if scenario == "same_asset_new_occurrence":
        items.append(
            ScheduleItemResponse(
                id=uuid.uuid4(),
                asset_id="welcome-asset",
                asset_title="Welcome",
                channel_id="gov",
                mode="premiere",
                state="published",
                scheduled_at=first_horizon + timedelta(seconds=60),
                duration_seconds=60,
                notes=None,
                created_at=first_horizon - timedelta(days=1),
            )
        )
    if scenario == "next_program":
        council_path = tmp_path / "council.ts"
        council_path.write_text("fake council media", encoding="utf-8")
        items.append(
            ScheduleItemResponse(
                id=uuid.uuid4(),
                asset_id="council-asset",
                asset_title="City Council",
                channel_id="gov",
                mode="premiere",
                state="published",
                scheduled_at=first_horizon + timedelta(seconds=60),
                duration_seconds=1800,
                notes=None,
                created_at=first_horizon - timedelta(days=1),
            )
        )
        assets["council-asset"] = StaffAssetRow(
            asset_id="council-asset",
            title="City Council",
            state="validated",
            file_path=str(council_path),
            duration_seconds=1800,
            trim_in_seconds=None,
            trim_out_seconds=1800,
        )

    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=assets.get,
        max_segments=1,
        # Production uses a 30s absorb window. Widen only the next-program branch
        # so the item at the 60s slot boundary is eligible at the short media
        # tail's end; other cases use production's configured window.
        gap_absorb_seconds=60.0 if scenario == "next_program" else 30.0,
        media_duration_resolver=lambda path: 20.0 if path == welcome_path else 1800.0,
    )

    def boundary(channel_id: str, at: datetime, **kwargs: Any) -> EgressSourcePlan | None:
        boundary_calls.append(at)
        plan = provider.plan_at(channel_id, at, **kwargs)
        boundary_plans.append(plan)
        return plan

    fallback_calls: list[str] = []

    def fallback(_config: EgressConfig) -> EgressSourcePlan:
        fallback_calls.append("called")
        return _plan_with_seconds(tmp_path, "CivicCast slate", 3600.0)

    daemon = _u36_rollover_daemon(
        tmp_path,
        strategy=strategy,
        boundary=boundary,
        fallback=fallback,
    )
    process = _u36_on_air(daemon, label="Previous programme")

    daemon.record_rollover_plan_end("gov", first_horizon, command_id=None)
    daemon._request_reload("gov")

    # A genuinely short, playable item is not mistaken for a dying tail.
    assert strategy.reload_calls == ["Welcome"]
    first_reload_id = strategy.reload_ids[-1]
    assert first_reload_id is not None
    assert daemon._pending_reload_settle["gov"].target_state == "ON_AIR"
    assert process.terminated is False
    assert daemon._store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]

    _write_fake_reload_status(tmp_path, "gov", first_reload_id, "applied")
    daemon._poll_reload_settlement("gov")
    first_settled = daemon._store.read_state("gov")
    assert first_settled is not None
    assert first_settled.state == "ON_AIR"
    assert first_settled.current_source_label == "Welcome"

    daemon.record_rollover_plan_end("gov", second_horizon, command_id=None)
    daemon._request_reload("gov")

    # The provider itself returns Welcome again, with five seconds left after
    # a 15-second join into its 60-second slot. The tail lookup resolves either
    # the real next item or no item, depending on the case.
    if scenario == "same_asset_new_occurrence":
        assert boundary_calls == [first_horizon, second_horizon]
    else:
        assert boundary_calls == [
            first_horizon,
            second_horizon,
            second_horizon + timedelta(seconds=6.0),
        ]
    assert boundary_plans[0] is not None
    assert boundary_plans[0].segments[0].label == "Welcome"
    assert boundary_plans[0].segments[0].duration_seconds == pytest.approx(20.0)
    assert boundary_plans[0].segments[0].schedule_occurrence_id == str(items[0].id)
    assert boundary_plans[1] is not None
    assert boundary_plans[1].segments[0].source_ref == "welcome-asset"
    assert boundary_plans[1].segments[0].inpoint_seconds == pytest.approx(15.0)
    assert boundary_plans[1].segments[0].duration_seconds == pytest.approx(5.0)
    if scenario == "same_asset_new_occurrence":
        assert boundary_plans[1].segments[0].schedule_occurrence_id == str(items[1].id)
        assert boundary_plans[1].segments[0].schedule_occurrence_id != (
            boundary_plans[0].segments[0].schedule_occurrence_id
        )
    else:
        assert boundary_plans[1].segments[0].schedule_occurrence_id == (
            boundary_plans[0].segments[0].schedule_occurrence_id
        )
    if scenario == "next_program":
        assert boundary_plans[2] is not None
        assert boundary_plans[2].segments[0].label == "City Council"
    elif scenario == "no_next":
        assert boundary_plans[2] is None
    assert fallback_calls == ([] if scenario != "no_next" else ["called"])
    assert strategy.reload_calls == ["Welcome", expected_label]
    assert process.terminated is False
    pending = daemon._pending_reload_settle["gov"]
    assert pending.target_state == expected_state
    assert pending.source_plan.segments[0].label == expected_label
    unsettled = daemon._store.read_state("gov")
    assert unsettled is not None
    assert unsettled.state == "ON_AIR"
    assert unsettled.current_source_label == "Welcome"

    second_reload_id = strategy.reload_ids[-1]
    assert second_reload_id is not None
    _write_fake_reload_status(tmp_path, "gov", second_reload_id, "applied")
    daemon._poll_reload_settlement("gov")

    settled = daemon._store.read_state("gov")
    assert settled is not None
    assert settled.state == expected_state
    assert settled.current_source_label == expected_label


def test_horizon_bound_rollover_resolves_past_a_sub_floor_schedule_tail(
    tmp_path: Path,
) -> None:
    """U36 decision 2(i): a horizon-bound rollover must not install a sub-floor
    tail of the item closing at its horizon -- the 14:06:50 case, which handed
    the new leg 1.6s of already-aired media. Resolve where that tail ends
    instead: the next program."""
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy([_FakeProcess(pid=222)], started)
    boundary_args: list[datetime] = []
    horizon = datetime.now(UTC) + timedelta(seconds=300.0)
    next_due = horizon + timedelta(seconds=10.2)

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan:
        boundary_args.append(at)
        if at >= next_due:
            return _plan_with_seconds(tmp_path, "City Council", 1800.0)
        return _plan_with_seconds(tmp_path, "Longmont Weather :16", 9.2)

    daemon = _u36_rollover_daemon(tmp_path, strategy=strategy, boundary=boundary)
    _u36_on_air(daemon)

    daemon.record_rollover_plan_end("gov", horizon, command_id=None)
    daemon._request_reload("gov")

    # Resolved once AT the horizon (a 9.2s tail), once where that tail ends.
    assert boundary_args == [horizon, next_due]
    assert strategy.reload_calls == ["City Council"]


def test_horizon_bound_rollover_keeps_a_tail_with_more_than_the_floor_left(
    tmp_path: Path,
) -> None:
    """The floor cannot skip a program that genuinely has more than 30s left:
    the plan resolved at the horizon is installed untouched and the boundary is
    never asked a second time."""
    started: list[_FakeProcess] = []
    strategy = _FakeContentReloadStrategy([_FakeProcess(pid=222)], started)
    boundary_args: list[datetime] = []
    horizon = datetime.now(UTC) + timedelta(seconds=300.0)

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan:
        boundary_args.append(at)
        return _plan_with_seconds(tmp_path, "Longmont Weather :16", 45.0)

    daemon = _u36_rollover_daemon(tmp_path, strategy=strategy, boundary=boundary)
    _u36_on_air(daemon)

    daemon.record_rollover_plan_end("gov", horizon, command_id=None)
    daemon._request_reload("gov")

    assert boundary_args == [horizon]
    assert strategy.reload_calls == ["Longmont Weather :16"]


def test_relaunch_onto_a_sub_floor_schedule_tail_starts_the_next_program(
    tmp_path: Path,
) -> None:
    """U36 decision 2(ii): the relaunch path is never given a sub-floor tail
    either. A crash relaunch resolving the program whose media is (by the
    schedule clock) seconds from its end starts the NEXT program instead of a
    sliver that immediately EOSes the replacement worker -- the 14:17:52 case,
    where reload 5's single segment was seconds of the program about to end."""
    started: list[_FakeProcess] = []
    strategy = _PlanLabelRecordingStrategy([_FakeProcess(pid=111), _FakeProcess(pid=222)], started)
    boundary_args: list[datetime] = []
    plans = [
        _plan_with_seconds(tmp_path, "Longmont Weather :16", 600.0),
        _plan_with_seconds(tmp_path, "Longmont Weather :16", 9.2),
    ]
    calls = {"n": 0}

    def next_plan() -> EgressSourcePlan:
        plan = plans[min(calls["n"], len(plans) - 1)]
        calls["n"] += 1
        return plan

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan:
        boundary_args.append(at)
        return _plan_with_seconds(tmp_path, "City Council", 1800.0)

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: next_plan(),
        boundary_source_plan_provider=boundary,
        encoder_strategy=strategy,
    )
    store.enqueue_command(_command())
    before = datetime.now(UTC)

    daemon.process_once("gov")
    assert store.read_state("gov").state == "ON_AIR"
    # A 600s program is worth airing on its own: the start never consults the
    # boundary, so the floor is invisible to an ordinary healthy start.
    assert boundary_args == []
    assert strategy.started_labels == ["Longmont Weather :16"]

    started[0].returncode = 1
    daemon.process_once("gov")  # crash -> crash-relaunch

    assert store.read_state("gov").state == "ON_AIR"
    assert len(boundary_args) == 1
    assert boundary_args[0] >= before + timedelta(seconds=10.2)
    assert strategy.started_labels == ["Longmont Weather :16", "City Council"]


def test_relaunch_keeps_a_tail_with_more_than_the_floor_left(tmp_path: Path) -> None:
    """45s of program is worth airing: the relaunch airs it and never consults
    the boundary, so the floor cannot skip a program with real remainder."""
    started: list[_FakeProcess] = []
    strategy = _PlanLabelRecordingStrategy([_FakeProcess(pid=111), _FakeProcess(pid=222)], started)
    boundary_args: list[datetime] = []
    plans = [
        _plan_with_seconds(tmp_path, "Longmont Weather :16", 600.0),
        _plan_with_seconds(tmp_path, "Longmont Weather :16", 45.0),
    ]
    calls = {"n": 0}

    def next_plan() -> EgressSourcePlan:
        plan = plans[min(calls["n"], len(plans) - 1)]
        calls["n"] += 1
        return plan

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan:
        boundary_args.append(at)
        return _plan_with_seconds(tmp_path, "City Council", 1800.0)

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _cid: next_plan(),
        boundary_source_plan_provider=boundary,
        encoder_strategy=strategy,
    )
    store.enqueue_command(_command())
    daemon.process_once("gov")

    started[0].returncode = 1
    daemon.process_once("gov")

    assert boundary_args == []
    assert strategy.started_labels == ["Longmont Weather :16", "Longmont Weather :16"]


def test_aborted_boundary_reload_is_retried_before_the_horizon(tmp_path: Path) -> None:
    """U36 item 7: a boundary reload the worker aborts pre-commit must not leave
    the channel with nothing prepared at the horizon (public's 14:44
    ``aborted:error`` was discarded with no retry, and its next boundary then
    arrived with nothing prepared). The daemon re-resolves and re-arms."""
    started: list[_FakeProcess] = []
    strategy = _AbortingReloadStrategy(
        [_FakeProcess(pid=222)], started, abort_results=["aborted:error"]
    )
    boundary_args: list[datetime] = []
    horizon = datetime.now(UTC) + timedelta(seconds=300.0)

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan:
        boundary_args.append(at)
        if at >= horizon + timedelta(seconds=10.2):
            return _plan_with_seconds(tmp_path, "City Council", 1800.0)
        return _plan_with_seconds(tmp_path, "Longmont Weather :16", 9.2)

    daemon = _u36_rollover_daemon(tmp_path, strategy=strategy, boundary=boundary)
    process = _u36_on_air(daemon)

    daemon.record_rollover_plan_end("gov", horizon, command_id=None)
    daemon._request_reload("gov")
    assert strategy.reload_calls == ["City Council"]

    first_id = daemon._pending_reload_settle["gov"].reload_id
    _write_fake_reload_status(tmp_path, "gov", first_id, "aborted:error")
    daemon.process_once("gov")

    # Retried, not fallen back to a restart: a new reload is armed, still
    # carrying this boundary's horizon.
    assert strategy.reload_calls == ["City Council", "City Council"]
    assert daemon._pending_reload_settle["gov"].reload_id != first_id
    assert daemon._pending_reload_settle["gov"].rollover_plan_end_at == horizon
    # The outgoing leg keeps airing while the retry arms -- that is the point
    # of retrying before the horizon instead of restarting the worker.
    assert process.terminated is False
    assert daemon._pending_reloads == {}


def test_exhausted_boundary_reload_retries_arm_the_slate_at_the_boundary(
    tmp_path: Path,
) -> None:
    """U36 item 7, the other arm: when the bounded retries are exhausted the
    channel gets the fallback slate armed AT the boundary. It must never be
    left with the outgoing leg running out into a stall exit -- the failure
    that cost the public channel ~80s of dead air on 2026-09-25."""
    started: list[_FakeProcess] = []
    strategy = _AbortingReloadStrategy(
        [_FakeProcess(pid=222)],
        started,
        abort_results=["aborted:error", "aborted:error", "aborted:error"],
    )
    boundary_args: list[datetime] = []
    horizon = datetime.now(UTC) + timedelta(seconds=300.0)
    exhausted = {"yes": False}

    def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan | None:
        boundary_args.append(at)
        if exhausted["yes"]:
            # Nothing left on the schedule where the exhausted boundary falls.
            return None
        if at >= horizon + timedelta(seconds=10.2):
            return _plan_with_seconds(tmp_path, "City Council", 1800.0)
        return _plan_with_seconds(tmp_path, "Longmont Weather :16", 9.2)

    daemon = _u36_rollover_daemon(
        tmp_path,
        strategy=strategy,
        boundary=boundary,
        fallback=lambda _config: _plan_with_seconds(tmp_path, "CivicCast slate", 3600.0),
    )
    process = _u36_on_air(daemon)

    daemon.record_rollover_plan_end("gov", horizon, command_id=None)
    daemon._request_reload("gov")

    # Three abort/settle cycles: two bounded retries, then the slate arm.
    for index in range(3):
        assert "gov" in daemon._pending_reload_settle
        if index == 2:
            exhausted["yes"] = True
        _write_fake_reload_status(
            tmp_path,
            "gov",
            daemon._pending_reload_settle["gov"].reload_id,
            "aborted:error",
        )
        daemon.process_once("gov")

    assert strategy.reload_calls == [
        "City Council",
        "City Council",
        "City Council",
        "CivicCast slate",
    ]
    pending = daemon._pending_reload_settle["gov"]
    assert pending.target_state == "FALLBACK_SLATE"
    assert pending.source_plan.segments[0].label == "CivicCast slate"
    # Never left to run out into an exit: the outgoing worker is still running
    # and the slate is armed against the boundary.
    assert process.terminated is False
    assert daemon._pending_reloads == {}


def test_caption_warmup_precedes_unprepared_speech_without_blocking_supervisor(tmp_path):
    entered, release = Event(), Event()
    started = []
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())

    def warm(channel, cancel):
        assert channel == "gov"
        entered.set()
        assert release.wait(5)

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _: _source_plan(tmp_path),
        ffmpeg_starter=lambda _: started.append(True) or _FakeProcess(),
        channel_caption_prepare_hook=warm,
    )
    daemon.enable_async_preparation()
    try:
        daemon.process_once("gov")
        assert entered.wait(5)
        assert started == []
        release.set()
        daemon._preparations["gov"].future.result(timeout=5)
        daemon.process_once("gov")
        assert started == [True]
    finally:
        release.set()
        daemon.shutdown_preparation()


def test_caption_warmup_supplied_plan_cancel_releases_once(tmp_path):
    from civiccast.egress.daemon import _PreparationRequest

    entered, release = Event(), Event()
    released = []
    store = InMemoryEgressStore()
    config = _config()
    store.upsert_config(config)
    report = SourcePreparationReport(_source_plan(tmp_path), (), tmp_path / "already-prepared")

    def warm(channel, cancel):
        entered.set()
        assert release.wait(5)

    def steps():
        yield _PreparationRequest(report.source_plan, config, report)
        pytest.fail("Cancelled warmup must not resume/start a worker")

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        channel_caption_prepare_hook=warm,
        prepared_plan_release=released.append,
        source_plan_provider=lambda _: report.source_plan,
    )
    daemon.enable_async_preparation()
    try:
        daemon._drive_preparation("gov", steps(), kind="start")
        assert entered.wait(5)
        pending = daemon._preparations["gov"]
        daemon._cancel_preparation("gov")
        release.set()
        with pytest.raises(SourcePrepareError):
            pending.future.result(timeout=5)
    finally:
        release.set()
        daemon.shutdown_preparation()
    assert released == [report.plan_dir]


def test_caption_warmup_does_not_run_on_synchronous_supervisor(tmp_path):
    from unittest.mock import Mock

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    warm = Mock(side_effect=AssertionError("must stay off supervisor thread"))
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _: _source_plan(tmp_path),
        ffmpeg_starter=lambda _: _FakeProcess(),
        channel_caption_prepare_hook=warm,
    )
    daemon.process_once("gov")
    warm.assert_not_called()


def test_caption_warmup_reused_plan_stays_protected_without_reconforming(tmp_path):
    entered, release = Event(), Event()
    run = _hold_fallback_slate_restart_plan(tmp_path)

    def warm(channel, cancel):
        entered.set()
        assert release.wait(5)

    run.daemon._channel_caption_prepare_hook = warm
    prepared_before = list(run.prepared_labels)
    try:
        run.daemon.process_once("gov")
        assert entered.wait(5)
        pending = run.daemon._preparations["gov"]
        assert pending.supplied_report is not None
        assert pending.supplied_report.plan_dir in run.daemon.live_prepared_plan_dirs("gov")
        assert run.started == []
        release.set()
        pending.future.result(timeout=5)
        run.daemon.process_once("gov")
        assert run.started == [run.new_process]
        assert run.prepared_labels == prepared_before
    finally:
        release.set()
        run.daemon.shutdown_preparation()
