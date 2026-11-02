# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S23 §6.1 as-run capture seam.

The playout engine emits a proof event at every ACTUAL source transition; the
daemon must append an as-run entry there with the engine-verified ACTUAL times
(``actual_start`` = the proof event's ``observed_at``, NOT the scheduled intent)
and ``verified=True`` (backed by the proof event). Capture is an append-only
side-effect: a recorder error must NEVER break the playout path.

These tests drive the daemon with a fake recorder + the in-memory egress store
(the unit harness ``test_daemon.py`` uses) and assert:

* a normal start records ONE transition with actual times + verified + the
  right source_kind/asset_id;
* the GStreamer seamless content-reload (the engine's default boundary) ALSO
  records a transition;
* the open row is closed (``actual_end``) at the next transition and at terminal
  stop / error;
* slate / filler / live map to the right source_kind, and slate/filler carry no
  library asset_id;
* a recorder that raises does not break playout (the channel still goes on air).

A separate suite exercises the concrete ``StoreAsRunRecorder`` over a real
SQLite-backed ``ReportingStore`` (the open/close stitch + station resolution).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from civiccast.db import Base
from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.encoder_strategy import EncoderStartRequest, EncoderStartResult
from civiccast.egress.models import (
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
)
from civiccast.egress.store import InMemoryEgressStore
from civiccast.reporting.asrun_recorder import StoreAsRunRecorder
from civiccast.reporting.store import ReportingStore

# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


def _write_fake_reload_status(
    work_dir: Path, channel_id: str, command_id: str | None, result: str
) -> None:
    """F1 redesign test helper: simulates ``worker.py``'s
    ``_write_reload_status`` -- the file ``EgressDaemon._poll_reload_
    settlement`` polls for. See test_daemon.py's identical helper."""
    channel_dir = work_dir / channel_id
    channel_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"id": command_id or uuid.uuid4().hex, "result": result})
    (channel_dir / "reload-status.json").write_text(payload, encoding="utf-8")


class _FakeProcess:
    def __init__(self, *, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0


class _RecordedTransition:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _FakeRecorder:
    """Captures record_transition / close_open calls for assertions."""

    def __init__(
        self,
        *,
        raise_on_record: bool = False,
        raise_on_close: bool = False,
    ) -> None:
        self.transitions: list[_RecordedTransition] = []
        self.closes: list[dict[str, object]] = []
        self._raise = raise_on_record
        self._raise_close = raise_on_close

    def record_transition(self, **kwargs: object) -> None:
        if self._raise:
            raise RuntimeError("boom — capture must not break playout")
        self.transitions.append(_RecordedTransition(**kwargs))

    def close_open(self, **kwargs: object) -> None:
        if self._raise_close:
            raise RuntimeError("close boom — capture must not break playout")
        self.closes.append(dict(kwargs))


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id="gov",
        enabled=True,
        slate_message="CivicCast is preparing the channel.",
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri="build/out.ts")],
    )


def _command(action: str = "start", command_id: str | None = None) -> EgressCommand:
    return EgressCommand(
        channel_id="gov",
        action=action,  # type: ignore[arg-type]
        issued_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        issued_by="operator",
        command_id=command_id or f"cmd-{action}",
    )


def _program_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "council.ts"
    source.write_text("fake", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Council meeting",
                path=str(source),
                duration_seconds=1,
                kind="program",
                source_ref="asset-council",
                schedule_occurrence_id="scheduled-council",
            )
        ],
    )


def _live_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "live.ts"
    source.write_text("live", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Live: chamber",
                path=str(source),
                duration_seconds=1,
                kind="live",
                source_ref="live-chamber",
            )
        ],
    )


def _filler_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "bulletin.ts"
    source.write_text("cg", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Community bulletin",
                path=str(source),
                duration_seconds=1,
                kind="cg",
                source_ref="bulletin-42",
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
                label="CivicCast slate",
                path=str(source),
                duration_seconds=1,
                kind="slate",
                source_ref="civiccast-slate",
            )
        ],
    )


def _daemon(
    store: InMemoryEgressStore,
    tmp_path: Path,
    recorder: _FakeRecorder,
    plan: EgressSourcePlan,
    process: _FakeProcess,
    *,
    fallback_plan: EgressSourcePlan | None = None,
) -> EgressDaemon:
    return EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _c: plan,
        fallback_source_provider=(lambda _config: fallback_plan) if fallback_plan else None,
        as_run_recorder=recorder,
        ffmpeg_starter=lambda _args: process,
    )


# --------------------------------------------------------------------------- #
# Daemon seam
# --------------------------------------------------------------------------- #


def test_start_records_as_run_with_actual_times_and_verified(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    recorder = _FakeRecorder()
    daemon = _daemon(store, tmp_path, recorder, _program_plan(tmp_path), _FakeProcess())

    before = datetime.now(UTC)
    daemon.process_once("gov")
    after = datetime.now(UTC)

    assert len(recorder.transitions) == 1
    t = recorder.transitions[0]
    assert t.channel_id == "gov"
    assert t.source_kind == "program"
    assert t.asset_id == "asset-council"
    assert t.schedule_item_id == "scheduled-council"
    assert t.source_label == "Council meeting"
    # actual_start is the engine proof-event observed_at — an ACTUAL air time,
    # not a scheduled intent — and falls within the start window.
    assert before <= t.actual_start <= after
    # The proof-event id ties the as-run entry to the proof chain (verified).
    proof = store.recent_proof_events("gov", 1)[0]
    assert t.proof_event_id == proof.event_id
    assert t.actual_start == proof.observed_at


def test_seamless_content_reload_records_a_transition(tmp_path: Path) -> None:
    """The GStreamer seamless swap (no encoder restart) is the engine's default
    program-boundary path; it must still produce an as-run entry."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())

    plan_holder: dict[str, EgressSourcePlan] = {"plan": _program_plan(tmp_path)}

    class _ReloadStrategy:
        supports_content_reload = True

        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            return EncoderStartResult(
                process=process,
                concat_plan_path=request.work_dir / "playout-graph.json",
                stdout_path=request.work_dir / "out.log",
                stderr_path=request.work_dir / "err.log",
                args=("worker",),
            )

        def reload_content(
            self,
            channel_id: str,
            work_dir: Path,
            request: EncoderStartRequest,
            *,
            command_id: str | None = None,
        ) -> bool:
            # F1 redesign: True means ARMED; simulate an immediately-settling
            # reload (see test_daemon.py's identical helper/comment).
            _write_fake_reload_status(work_dir, channel_id, command_id, "applied")
            return True

    process = _FakeProcess()
    recorder = _FakeRecorder()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _c: plan_holder["plan"],
        as_run_recorder=recorder,
        encoder_strategy=_ReloadStrategy(),
        ffmpeg_starter=lambda _args: process,
    )

    store.enqueue_command(_command("start"))
    daemon.process_once("gov")
    assert len(recorder.transitions) == 1  # initial program

    # A new program is now due; a reload seamlessly swaps it in place.
    next_source = tmp_path / "next.ts"
    next_source.write_text("next", encoding="utf-8")
    plan_holder["plan"] = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Next program",
                path=str(next_source),
                duration_seconds=1,
                kind="program",
                source_ref="asset-next",
            )
        ],
    )
    store.enqueue_command(_command("reload"))
    daemon.process_once("gov")
    # F1 redesign: the reload is ARMED after the tick above (the fake strategy
    # already wrote reload-status.json "applied"); the as-run transition only
    # lands once _poll_reload_settlement observes it -- one more tick.
    daemon.process_once("gov")

    assert len(recorder.transitions) == 2  # the seamless boundary was captured
    assert recorder.transitions[1].source_label == "Next program"
    assert recorder.transitions[1].asset_id == "asset-next"


def test_clean_stop_closes_the_open_as_run_row(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    process = _FakeProcess()
    recorder = _FakeRecorder()
    daemon = _daemon(store, tmp_path, recorder, _program_plan(tmp_path), process)

    store.enqueue_command(_command("start"))
    daemon.process_once("gov")
    assert len(recorder.transitions) == 1

    # Operator stop terminates the encoder (exit 0); the next poll closes the row.
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")  # processes the stop command (terminate)
    daemon.process_once("gov")  # polls the now-exited process → clean stop close

    assert len(recorder.closes) == 1
    assert recorder.closes[0]["channel_id"] == "gov"


def test_terminal_error_closes_the_open_as_run_row(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    process = _FakeProcess()
    recorder = _FakeRecorder()
    daemon = _daemon(store, tmp_path, recorder, _program_plan(tmp_path), process)

    store.enqueue_command(_command("start"))
    daemon.process_once("gov")

    # The encoder dies non-zero while NOT expected on-air (state was reset) — a
    # terminal error close. Drive that by stopping first (state STOPPING) then a
    # non-zero exit: simulate by directly forcing a crash from a non-onair state.
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")  # STOPPING; terminate set returncode 0
    # Force a non-zero crash exit from a non-onair state to hit the ERROR close.
    process.returncode = 1
    store.write_state(store.read_state("gov"))  # state is STOPPED/STOPPING (not onair)
    daemon._processes["gov"] = process  # re-track so _poll_process sees the exit
    daemon._poll_process("gov")

    assert any(c["channel_id"] == "gov" for c in recorder.closes)


@pytest.mark.parametrize(
    ("plan_fn", "expected_kind", "expected_asset"),
    [
        # Only a program segment's source_ref is a library asset id; live feeds,
        # filler bulletins, and slate are NOT library assets → asset_id is None.
        (_live_plan, "live", None),
        (_filler_plan, "filler", None),
        (_slate_plan, "slate", None),
    ],
)
def test_source_kind_mapping_and_asset_guard(
    tmp_path: Path,
    plan_fn,
    expected_kind: str,
    expected_asset: str | None,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    recorder = _FakeRecorder()
    daemon = _daemon(store, tmp_path, recorder, plan_fn(tmp_path), _FakeProcess())

    daemon.process_once("gov")

    assert len(recorder.transitions) == 1
    t = recorder.transitions[0]
    assert t.source_kind == expected_kind
    # Only program segments carry a real library asset id; slate/filler/live with
    # no library asset record asset_id=None (the source_ref is not a library id).
    assert t.asset_id == expected_asset


def test_forced_fallback_slate_maps_to_slate_kind(tmp_path: Path) -> None:
    """A fallback slate runs with running_state FALLBACK_SLATE — it must map to
    ``slate`` even though the segment may not declare kind='slate'."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    recorder = _FakeRecorder()
    # source_plan_provider returns None → daemon uses the fallback provider.
    fallback = _slate_plan(tmp_path)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _c: None,
        fallback_source_provider=lambda _config: fallback,
        as_run_recorder=recorder,
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    daemon.process_once("gov")

    assert recorder.transitions[0].source_kind == "slate"


def test_recorder_error_does_not_break_playout(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    recorder = _FakeRecorder(raise_on_record=True)
    daemon = _daemon(store, tmp_path, recorder, _program_plan(tmp_path), _FakeProcess())

    # Must not raise; the channel still goes on air and the proof event is written.
    daemon.process_once("gov")

    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_proof_event_id is not None
    assert store.recent_proof_events("gov", 1)  # proof chain intact


def test_recorder_close_error_does_not_break_playout(tmp_path: Path) -> None:
    """T-2: the daemon's ``_close_as_run`` fail-safe (the close half of the
    two-layer guard) must swallow a raising ``close_open`` and let the
    channel reach its terminal state cleanly.

    A future refactor that drops the daemon-side ``try/except`` would slip
    past the existing record-side-only coverage. This forces the close-side
    branch to be exercised.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    process = _FakeProcess()
    recorder = _FakeRecorder(raise_on_close=True)
    daemon = _daemon(store, tmp_path, recorder, _program_plan(tmp_path), process)

    store.enqueue_command(_command("start"))
    daemon.process_once("gov")
    assert recorder.transitions  # the channel went on air

    # Stop command terminates the encoder (exit 0); the next poll closes the
    # row — that's where ``close_open`` raises. The daemon must NOT propagate.
    store.enqueue_command(_command("stop"))
    daemon.process_once("gov")  # processes the stop (terminate)
    daemon.process_once("gov")  # polls the exited process → close path raises

    # No exception escaped; channel reached a terminal state cleanly.
    final_state = store.read_state("gov")
    assert final_state is not None
    assert final_state.state == "STOPPED"


def test_no_recorder_is_a_silent_noop(tmp_path: Path) -> None:
    """The in-memory/CLI path constructs the daemon with no recorder — behavior
    must be identical to before S23 (no error, channel on air)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.enqueue_command(_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _c: _program_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
    )

    daemon.process_once("gov")

    assert store.read_state("gov").state == "ON_AIR"


# --------------------------------------------------------------------------- #
# Concrete StoreAsRunRecorder over a real SQLite-backed ReportingStore
# --------------------------------------------------------------------------- #


@pytest.fixture
def reporting_store(tmp_path: Path) -> Iterator[ReportingStore]:
    eng = create_engine(f"sqlite:///{tmp_path / 'reporting.sqlite'}", future=True)
    Base.metadata.create_all(eng)

    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=eng) as session:
            yield session

    try:
        yield ReportingStore(factory)
    finally:
        eng.dispose()


def test_recorder_keeps_planned_start_separate_from_observed_output(reporting_store) -> None:
    from types import SimpleNamespace

    planned = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    actual = planned + timedelta(seconds=7)
    item = SimpleNamespace(channel_id="gov", asset_id="asset-a", scheduled_at=planned)
    recorder = StoreAsRunRecorder(
        reporting_store, station_id="civiccast-station", schedule_lookup=lambda _id: item
    )
    recorder.record_transition(
        channel_id="gov",
        source_kind="program",
        asset_id="asset-a",
        source_label="A",
        actual_start=actual,
        proof_event_id="proof-1",
        schedule_item_id="schedule-a",
    )
    entry = reporting_store.list_as_run("civiccast-station")[0]
    assert entry.scheduled_start == planned
    assert entry.actual_start == actual
    assert entry.schedule_item_id == "schedule-a"


def test_store_recorder_appends_and_stitches_durations(
    reporting_store: ReportingStore,
) -> None:
    recorder = StoreAsRunRecorder(reporting_store, station_id="civiccast-station")
    t0 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=30)
    t2 = t1 + timedelta(minutes=45)

    recorder.record_transition(
        channel_id="gov",
        source_kind="program",
        asset_id="asset-a",
        source_label="A",
        actual_start=t0,
        proof_event_id="proof-1",
    )
    # The second transition closes the first row's actual_end == t1.
    recorder.record_transition(
        channel_id="gov",
        source_kind="program",
        asset_id="asset-b",
        source_label="B",
        actual_start=t1,
        proof_event_id="proof-2",
    )
    recorder.close_open(channel_id="gov", actual_end=t2)

    entries = reporting_store.list_as_run("civiccast-station")
    assert len(entries) == 2
    first, second = entries
    assert first.asset_id == "asset-a"
    assert first.actual_start == t0
    assert first.actual_end == t1
    assert first.duration_s == 30 * 60
    assert first.verified is True
    assert second.asset_id == "asset-b"
    assert second.actual_start == t1
    assert second.actual_end == t2
    assert second.duration_s == 45 * 60


def test_store_recorder_resolves_station_from_env(
    reporting_store: ReportingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CIVICCAST_STATION_ID", "city-of-example")
    recorder = StoreAsRunRecorder(reporting_store)
    recorder.record_transition(
        channel_id="gov",
        source_kind="slate",
        asset_id=None,
        source_label="Slate",
        actual_start=datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
        proof_event_id="proof-x",
    )

    assert reporting_store.list_as_run("city-of-example")
    assert not reporting_store.list_as_run("civiccast-station")


def test_store_recorder_close_with_no_open_row_is_noop(
    reporting_store: ReportingStore,
) -> None:
    recorder = StoreAsRunRecorder(reporting_store, station_id="civiccast-station")
    # No transition recorded yet — closing must not raise or write anything.
    recorder.close_open(channel_id="gov", actual_end=datetime.now(UTC))
    assert reporting_store.list_as_run("civiccast-station") == []


def test_reload_uses_real_recorder_stitch_end_to_end(
    reporting_store: ReportingStore,
    tmp_path: Path,
) -> None:
    """T-9: end-to-end the GStreamer seamless content-reload path with the
    REAL :class:`StoreAsRunRecorder` (not the fake), and assert the daemon's
    reload boundary closes the first as-run row at the exact ``actual_start``
    of the second.

    The pieces work individually elsewhere; this proves their composition
    under the engine's default program-boundary (reload) path produces the
    stitched two-row ledger the franchise audit expects — no orphan-open
    ``duration_s=0`` row left behind.
    """
    egress_store = InMemoryEgressStore()
    egress_store.upsert_config(_config())

    plan_holder: dict[str, EgressSourcePlan] = {"plan": _program_plan(tmp_path)}

    class _ReloadStrategy:
        supports_content_reload = True

        def start(self, request: EncoderStartRequest) -> EncoderStartResult:
            return EncoderStartResult(
                process=process,
                concat_plan_path=request.work_dir / "playout-graph.json",
                stdout_path=request.work_dir / "out.log",
                stderr_path=request.work_dir / "err.log",
                args=("worker",),
            )

        def reload_content(
            self,
            channel_id: str,
            work_dir: Path,
            request: EncoderStartRequest,
            *,
            command_id: str | None = None,
        ) -> bool:
            # F1 redesign: True means ARMED; simulate an immediately-settling
            # reload (see test_daemon.py's identical helper/comment).
            _write_fake_reload_status(work_dir, channel_id, command_id, "applied")
            return True

    process = _FakeProcess()
    recorder = StoreAsRunRecorder(reporting_store, station_id="civiccast-station")
    daemon = EgressDaemon(
        egress_store,
        work_dir=tmp_path,
        source_plan_provider=lambda _c: plan_holder["plan"],
        as_run_recorder=recorder,
        encoder_strategy=_ReloadStrategy(),
        ffmpeg_starter=lambda _args: process,
    )

    egress_store.enqueue_command(_command("start"))
    daemon.process_once("gov")

    # Now a new program is due; a reload seamlessly swaps it in place.
    next_source = tmp_path / "next.ts"
    next_source.write_text("next", encoding="utf-8")
    plan_holder["plan"] = EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Next program",
                path=str(next_source),
                duration_seconds=1,
                kind="program",
                source_ref="asset-next",
            )
        ],
    )
    egress_store.enqueue_command(_command("reload"))
    daemon.process_once("gov")
    # F1 redesign: one more tick for _poll_reload_settlement to observe the
    # armed reload's (immediately-written, by the fake) settlement.
    daemon.process_once("gov")

    # Two ledger rows; the first row closes EXACTLY at the second row's start.
    # Find by asset_id (the playout-thread proof timestamps can tie at
    # microsecond resolution on fast Windows runs, so list ordering by
    # actual_start alone is not deterministic — but the stitch contract is).
    entries = reporting_store.list_as_run("civiccast-station")
    assert len(entries) == 2
    by_asset = {e.asset_id: e for e in entries}
    council = by_asset["asset-council"]
    next_row = by_asset["asset-next"]
    # The stitch: council.actual_end == next.actual_start. No orphan-open row.
    assert council.actual_end == next_row.actual_start
    assert council.duration_s == int((next_row.actual_start - council.actual_start).total_seconds())
    assert next_row.duration_s == 0  # still open until next transition / close
    assert next_row.actual_end == next_row.actual_start


def test_native_output_events_update_labels_once_and_ignore_partial_lines(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    recorder = _FakeRecorder()
    process = _FakeProcess()
    daemon = _daemon(store, tmp_path, recorder, _program_plan(tmp_path), process)
    daemon._encoder_strategy.observes_source_output = True
    from unittest.mock import Mock

    relay = Mock()
    daemon._hls_relay = relay
    log = tmp_path / "native-worker.log"
    daemon._stderr_logs["gov"] = log
    events = [
        {
            "pid": 9999,
            "media_pts_seconds": 3600.0 + i,
            "media_wall_seconds": 1793318400.0 + i,
            "kind": "program",
            "asset_id": f"asset-{i}",
            "label": f"Program {i}",
            "event_id": f"event-{i}",
            "observed_at": 1793318400 + i,
            "schedule_item_id": f"occurrence-{i}",
            "schedule_occurrence_start": "2026-10-30T00:00:00+00:00",
        }
        for i in range(2)
    ]
    lines = ["CIVICCAST_SOURCE_OUTPUT " + json.dumps(event) + "\n" for event in events]
    log.write_text(lines[0] + lines[1][:30])
    daemon._poll_source_output("gov", process)
    assert len(recorder.transitions) == 1
    relay.set_media_clock_anchor.assert_called_once_with("gov", process.pid, 3600.0, 1793318400.0)
    assert store.read_state("gov").current_source_label == "Program 0"
    with log.open("a") as stream:
        stream.write(lines[1][30:])
    daemon._poll_source_output("gov", process)
    daemon._poll_source_output("gov", process)
    assert [row.asset_id for row in recorder.transitions] == ["asset-0", "asset-1"]
    assert recorder.transitions[0].schedule_occurrence_start == datetime(2026, 10, 30, tzinfo=UTC)
    from civiccast.live.router import _public_program_timeline, _recent_public_program_events

    timeline = _public_program_timeline(_recent_public_program_events(store, "gov"))
    assert [entry.title for entry in timeline] == ["Program 0", "Program 1"]
    assert (
        timeline[1].started_at == datetime.fromtimestamp(events[1]["observed_at"], UTC).isoformat()
    )
    assert len(store.recent_proof_events("gov", 10)) == 2
    daemon._write_state(
        "gov", "ON_AIR", current_source_label="Old plan first item", pid=process.pid
    )
    assert store.read_state("gov").current_source_label == "Program 1"
