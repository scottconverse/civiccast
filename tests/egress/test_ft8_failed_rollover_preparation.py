# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""FT-8: a rollover whose preparation FAILED is recovered soon after the slate
goes on air, not after the U67 watchdog's flat 27.5-minute bound.

Field incident (r7, 2026-11-06): public's routine rollover was dispatched 86 s
before its 90 s programme ended. Its preparation failed (09:03:02), the daemon
fell back to "restart", armed the pending-reload pin and waited for a worker
exit that the plan-EOS hold never delivers. The channel reported TRANSITIONING
on the slate from 09:04 with nothing in flight; automation's slate replan only
acts on FALLBACK_SLATE, so the only recovery was the U67 watchdog's
690 s + 960 s = 1650 s bound.

A failed preparation has nothing in flight to wait for. The pin's honest
lifetime is the programme still airing plus a short margin: the watchdog
re-issues the rollover a minute after the recorded plan end. It must never
fire before that end, so a bad NEXT programme cannot cut a healthy one short.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from civiccast.egress import daemon as daemon_module
from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.encoder_strategy import EncoderStartRequest, EncoderStartResult
from civiccast.egress.errors import SourcePrepareError
from civiccast.egress.models import (
    CanonicalProfile,
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
)
from civiccast.egress.preparer import SourcePreparationReport
from civiccast.egress.store import InMemoryEgressStore

_CHANNEL = "public"
_LABEL = "Phase 4 public 90 s test"
_MARGIN = getattr(daemon_module, "_FAILED_PREPARATION_STALL_MARGIN_SECONDS", 60.0)


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminate_requests = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_requests += 1
        self.returncode = 0


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Strategy:
    name = "fake-content-reload"
    supports_live_swap = False
    supports_content_reload = True

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.process = _FakeProcess(17108)
        self.start_requests: list[EncoderStartRequest] = []
        self.reload_requests: list[EncoderStartRequest] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        self.start_requests.append(request)
        return EncoderStartResult(
            process=self.process,
            concat_plan_path=self.work_dir / "playout-graph.json",
            stdout_path=self.work_dir / "out.log",
            stderr_path=self.work_dir / "err.log",
            args=("fake-worker",),
        )

    def reload_content(self, channel_id, work_dir, request, *, command_id=None) -> bool:
        self.reload_requests.append(request)
        return True


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id=_CHANNEL,
        enabled=True,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri=f"build/{_CHANNEL}.ts")],
    )


def _plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "rc4-public-90s.ts"
    source.write_text(_LABEL, encoding="utf-8")
    return EgressSourcePlan(
        channel_id=_CHANNEL,
        segments=[
            EgressSourceSegment(
                label=_LABEL,
                path=str(source),
                duration_seconds=90.0,
                source_ref="ref-rc4-public-90s",
            )
        ],
    )


def _harness(tmp_path: Path):
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    plan = _plan(tmp_path)
    strategy = _Strategy(tmp_path)
    clock = _Clock()
    calls = {"resolve": 0, "prepare": 0, "fail": False}

    def wallclock_provider(channel_id: str) -> EgressSourcePlan:
        calls["resolve"] += 1
        return plan

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        calls["resolve"] += 1
        return plan

    def preparer(source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
        calls["prepare"] += 1
        if calls["fail"]:
            raise SourcePrepareError("Invalid duration for option ss: 5.6e-05")
        return SourcePreparationReport(source_plan, ())

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        source_preparer=preparer,
        encoder_strategy=strategy,
        monotonic=clock,
    )
    store.enqueue_command(
        EgressCommand(
            channel_id=_CHANNEL,
            action="start",
            issued_at=datetime.now(UTC),
            issued_by="operator",
            command_id=f"start-{_CHANNEL}",
        )
    )
    daemon.process_once(_CHANNEL)
    assert store.read_state(_CHANNEL).state == "ON_AIR"
    return store, daemon, strategy, clock, calls


def _fail_rollover(store, daemon, calls, plan_left: timedelta) -> None:
    calls["fail"] = True
    boundary = datetime.now(UTC) + plan_left
    command_id = "rollover-public-0902"
    daemon.record_rollover_plan_end(_CHANNEL, boundary, command_id=command_id)
    store.enqueue_command(
        EgressCommand(
            channel_id=_CHANNEL,
            action="reload",
            issued_at=datetime.now(UTC),
            issued_by="channel-automation",
            command_id=command_id,
        )
    )
    daemon.process_once(_CHANNEL)


def test_ft8_failed_rollover_preparation_is_reissued_a_minute_after_plan_end(tmp_path):
    store, daemon, strategy, clock, calls = _harness(tmp_path)
    _fail_rollover(store, daemon, calls, timedelta(seconds=86))

    # Stuck exactly as r7 was: pinned, nothing in flight, worker still alive.
    assert _CHANNEL in daemon._pending_reloads
    assert store.read_state(_CHANNEL).state == "TRANSITIONING"
    assert strategy.process.terminate_requests == 0
    prepares_after_failure = calls["prepare"]

    # While the outgoing programme can still be airing (86 s + margin), nothing.
    clock.advance(86.0 + _MARGIN - 10.0)
    daemon.process_once(_CHANNEL)
    assert calls["prepare"] == prepares_after_failure
    assert daemon._reload_stall_rungs.get(_CHANNEL, 0) == 0

    # About a minute after the slate went on air: the rollover is re-issued.
    calls["fail"] = False
    clock.advance(20.0)
    daemon.process_once(_CHANNEL)
    assert calls["prepare"] == prepares_after_failure + 1, (
        "a failed rollover preparation must be retried soon after the slate goes on "
        "air, not after the 1650 s reload-stall bound"
    )
    assert _CHANNEL not in daemon._pending_reloads
    assert len(strategy.reload_requests) == 1


def test_ft8_failed_preparation_never_cuts_a_programme_still_airing(tmp_path):
    store, daemon, strategy, clock, calls = _harness(tmp_path)
    _fail_rollover(store, daemon, calls, timedelta(hours=2))
    prepares_after_failure = calls["prepare"]

    # An hour later the outgoing programme still has an hour to run: no
    # re-issue and no restart may touch it.
    for _ in range(6):
        clock.advance(600.0)
        daemon.process_once(_CHANNEL)
    assert calls["prepare"] == prepares_after_failure
    assert strategy.process.terminate_requests == 0
    assert daemon._reload_stall_rungs.get(_CHANNEL, 0) == 0


def test_ft8_failed_preparation_without_a_recorded_end_keeps_the_flat_bound(tmp_path):
    store, daemon, _strategy, _clock, calls = _harness(tmp_path)
    calls["fail"] = True
    store.enqueue_command(
        EgressCommand(
            channel_id=_CHANNEL,
            action="reload",
            issued_at=datetime.now(UTC),
            issued_by="operator",
            command_id="operator-reload",
        )
    )
    daemon.process_once(_CHANNEL)
    assert _CHANNEL in daemon._pending_reloads
    assert daemon._reload_stall_bound_s[_CHANNEL] == daemon_module._TRANSITIONING_WATCHDOG_SECONDS
