# Copyright (c) The CivicCast Authors
# SPDX-License-Identifier: Apache-2.0
"""#344: a lost content reload must not keep a channel stuck on its old air.

Measured on R7, 2026-11-15 23:42: about 30 schedule commits arrived four
minutes before the first program. Each queued a ``commit-reload-*`` with no
recorded boundary, so each resolved "now", found nothing due, and declined:

    resolved no plan (boundary=-, force_fallback=False, resolved_from=now);
        declining into the terminate+restart fallback
    Seamless content-reload ... did not arm ... stays TRANSITIONING
    Content-reload settlement ... arrived after that attempt was already
        discarded ...; ignoring.

The decline discarded the reload already armed for the 23:46 start and pinned
the channel TRANSITIONING on a worker that, under the U41 plan-EOS hold, never
exits -- so the program due at 23:46 never aired.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.encoder_strategy import EncoderStartRequest, EncoderStartResult
from civiccast.egress.models import (
    CanonicalProfile,
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
)
from civiccast.egress.store import InMemoryEgressStore

_CHANNEL = "public"
_AIRING_LABEL = "Community Bulletin"
_NEXT_LABEL = "Planning Commission - November 2026"


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminate_requests = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        # U41 plan-EOS hold: the worker does not exit on its own; a terminate
        # is the only thing that would end it, and this fix must not send one.
        self.terminate_requests += 1


class _ReloadStrategy:
    name = "fake-content-reload"
    supports_live_swap = False
    supports_content_reload = True

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.process = _FakeProcess(4242)
        self.start_requests: list[EncoderStartRequest] = []
        self.reload_requests: list[EncoderStartRequest] = []
        self.reload_ids: list[str | None] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        self.start_requests.append(request)
        return EncoderStartResult(
            process=self.process,
            concat_plan_path=self.work_dir / "playout-graph.json",
            stdout_path=self.work_dir / "out.log",
            stderr_path=self.work_dir / "err.log",
            args=("fake-worker",),
        )

    def reload_content(
        self,
        channel_id: str,
        work_dir: Path,
        request: EncoderStartRequest,
        *,
        command_id: str | None = None,
    ) -> bool:
        self.reload_requests.append(request)
        self.reload_ids.append(command_id)
        return True


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id=_CHANNEL,
        enabled=True,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri=f"build/{_CHANNEL}.ts")],
    )


def _plan(tmp_path: Path, label: str) -> EgressSourcePlan:
    slug = label.lower().replace(" ", "-")
    source = tmp_path / f"{slug}.ts"
    source.write_text(label, encoding="utf-8")
    return EgressSourcePlan(
        channel_id=_CHANNEL,
        segments=[
            EgressSourceSegment(
                label=label, path=str(source), duration_seconds=1800.0, source_ref=f"ref-{slug}"
            )
        ],
    )


def _command(action: str, command_id: str) -> EgressCommand:
    return EgressCommand(
        channel_id=_CHANNEL,
        action=action,
        issued_at=datetime.now(UTC),
        issued_by="schedule-commit" if action == "reload" else "operator",
        command_id=command_id,
    )


def _write_status(tmp_path: Path, reload_id: str | None, result: str = "applied") -> None:
    status_path = tmp_path / "egress" / _CHANNEL / "reload-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps({"id": reload_id, "result": result}), encoding="utf-8")


class _Station:
    """A channel on air with a reload armed for the first program, minutes away."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.store = InMemoryEgressStore()
        self.store.upsert_config(_config())
        self.airing = _plan(tmp_path, _AIRING_LABEL)
        self.due_next = _plan(tmp_path, _NEXT_LABEL)
        self.strategy = _ReloadStrategy(tmp_path)
        self.now_plan: EgressSourcePlan | None = self.airing
        self.first_program_at = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=4)

        def wallclock(channel_id: str) -> EgressSourcePlan | None:
            return self.now_plan

        def at_boundary(channel_id: str, boundary_at: datetime, **kwargs: float):
            return self.due_next if boundary_at >= self.first_program_at else None

        self.daemon = EgressDaemon(
            self.store,
            work_dir=tmp_path / "egress",
            source_plan_provider=wallclock,
            boundary_source_plan_provider=at_boundary,
            encoder_strategy=self.strategy,
        )
        self.store.enqueue_command(_command("start", f"start-{_CHANNEL}"))
        self.daemon.process_once(_CHANNEL)
        assert self.store.read_state(_CHANNEL).state == "ON_AIR"
        # Nothing is due at wall-clock now any more: the schedule's first
        # program starts at ``first_program_at``.
        self.now_plan = None

    def arm_first_program(self) -> str:
        command_id = "rollover-public-2346"
        self.daemon.record_rollover_plan_end(
            _CHANNEL, self.first_program_at, command_id=command_id, min_plan_seconds=60.0
        )
        self.store.enqueue_command(_command("reload", command_id))
        self.daemon.process_once(_CHANNEL)
        assert len(self.strategy.reload_requests) == 1
        armed_id = self.strategy.reload_ids[-1]
        assert armed_id
        return armed_id

    def commit_burst(self, count: int) -> None:
        for index in range(count):
            self.store.enqueue_command(_command("reload", f"commit-reload-{index:03d}"))
            self.daemon.process_once(_CHANNEL)


def test_344_commit_burst_keeps_the_armed_first_program(tmp_path: Path) -> None:
    station = _Station(tmp_path)
    armed_id = station.arm_first_program()

    station.commit_burst(30)

    state = station.store.read_state(_CHANNEL)
    assert state.state == "ON_AIR", "a reload with nothing due now must not pin TRANSITIONING"
    assert state.pid == 4242
    assert station.strategy.process.terminate_requests == 0
    assert _CHANNEL not in station.daemon._pending_reloads
    pending = station.daemon._pending_reload_settle.get(_CHANNEL)
    assert pending is not None and pending.reload_id == armed_id, (
        "the reload armed for the first program must survive the commit burst"
    )
    assert len(station.strategy.reload_requests) == 1

    # The worker cuts to the first program at its start and reports it.
    _write_status(tmp_path, armed_id)
    station.daemon.process_once(_CHANNEL)
    state = station.store.read_state(_CHANNEL)
    assert state.state == "ON_AIR"
    assert state.current_source_label == _NEXT_LABEL


def test_344_commit_burst_with_nothing_armed_keeps_air_without_a_pin(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    station = _Station(tmp_path)

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.daemon"):
        station.commit_burst(30)

    state = station.store.read_state(_CHANNEL)
    assert state.state == "ON_AIR"
    assert state.current_source_label == _AIRING_LABEL
    assert _CHANNEL not in station.daemon._pending_reloads
    assert station.strategy.process.terminate_requests == 0
    assert not any("did not arm" in record.message for record in caplog.records)
    assert any("found nothing due now" in record.message for record in caplog.records)


def test_344_late_settlement_unpins_a_stuck_channel(tmp_path: Path) -> None:
    """The R7 tail: an armed reload discarded into a pin, then its late settlement."""

    station = _Station(tmp_path)
    armed_id = station.arm_first_program()

    # An older-build decline (any restart fallback) discards the armed reload
    # and pins the channel TRANSITIONING on the live worker.
    station.daemon._fall_back_to_restart_reload(_CHANNEL)
    assert station.store.read_state(_CHANNEL).state == "TRANSITIONING"
    assert _CHANNEL in station.daemon._pending_reloads

    # The discarded attempt's worker answers late.
    _write_status(tmp_path, armed_id)
    station.daemon.process_once(_CHANNEL)

    state = station.store.read_state(_CHANNEL)
    assert state.state == "ON_AIR", "a late settlement must not leave the channel stuck"
    assert state.pid == 4242
    assert _CHANNEL not in station.daemon._pending_reloads
    assert station.strategy.process.terminate_requests == 0
