# Copyright (c) The CivicCast Authors
# SPDX-License-Identifier: Apache-2.0
"""U67 RED/GREEN: the 2026-09-30 education rollover that never rolled.

Measured live shape (control_plane-app.log, 2026-09-30):

    03:44:10,575  automation rollover for education resolved to a 0.0s tail of the
                  closing scheduled item; using the plan due where that tail ends
                  instead (1800.0s)
    03:44:10,578  rollover for education was issued as soon as the previous plan
                  settled: the plan on air is 554s long, shorter than the 690s ...
    03:44:13,148  channel education: egress state -> TRANSITIONING (pid=24516, last_error=-)
                  ...and TRANSITIONING again every ~2s, same pid, to the log's end.

Automation re-resolved its boundary one margin past it (U63 defect A) and issued
the rollover; the daemon asked the SAME boundary instant, got the same ``None``
from the schedule (the item test is the half-open ``starts_at <= t < ends_at``),
declined the seamless reload with no log at all, and fell back to a
terminate+restart whose only recovery route -- the worker reaching plan EOS and
exiting -- the U41 plan-EOS hold had removed. Black and silent for 34 hours.

These tests pin the two halves of the fix against the LIVE bytes: the re-resolve
(work item 2) and the independent watchdog (work item 3). They are unit tests of
the control plane only -- no station process, no media, no config is touched.

The watchdog symbols are read through ``getattr`` on purpose: on the pre-fix
revision they do not exist, and a collection-time ImportError would hide the
behavioural failure behind a stack trace. Read this way, the RED run fails on
what the station actually did wrong -- no queued preparation, and a channel that
stays pinned forever.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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

_CHANNEL = "education"
_AIRING_LABEL = "Parks & Recreation Advisory Board - April 2026"
_NEXT_LABEL = "City Council - May 2026"

# The U67 watchdog's own surface, absent on the pre-fix revision. The stated
# bound is 1650 s (the 690 s rollover lead + the 960 s settlement deadline);
# the test below pins the module's value to it rather than assuming it.
_HAS_WATCHDOG = hasattr(daemon_module, "_reload_stall_bound_seconds")
_WATCHDOG_SECONDS = getattr(daemon_module, "_TRANSITIONING_WATCHDOG_SECONDS", 1650.0)
_GRACE_SECONDS = getattr(daemon_module, "_TRANSITIONING_WATCHDOG_REISSUE_GRACE_SECONDS", 300.0)
_bound_seconds = getattr(daemon_module, "_reload_stall_bound_seconds", None)


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.ignore_terminate = False
        self.terminate_requests = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_requests += 1
        if not self.ignore_terminate:
            self.returncode = 0


class _Clock:
    """The daemon's injectable monotonic clock, driven by the test."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _BoundaryStrategy:
    name = "fake-content-reload"
    supports_live_swap = False
    supports_content_reload = True

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.process = _FakeProcess(24516)
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


def _slug(label: str) -> str:
    return label.lower().replace(" ", "-").replace("&", "and")


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id=_CHANNEL,
        enabled=True,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri=f"build/{_CHANNEL}.ts")],
    )


def _plan(tmp_path: Path, label: str, *, duration_seconds: float = 1800.0) -> EgressSourcePlan:
    source = tmp_path / f"{_slug(label)}.ts"
    source.write_text(label, encoding="utf-8")
    return EgressSourcePlan(
        channel_id=_CHANNEL,
        segments=[
            EgressSourceSegment(
                label=label,
                path=str(source),
                duration_seconds=duration_seconds,
                source_ref=f"ref-{_slug(label)}",
            )
        ],
    )


def _start_command() -> EgressCommand:
    return EgressCommand(
        channel_id=_CHANNEL,
        action="start",
        issued_at=datetime.now(UTC),
        issued_by="operator",
        command_id=f"start-{_CHANNEL}",
    )


def _reload_command(command_id: str, issued_at: datetime) -> EgressCommand:
    return EgressCommand(
        channel_id=_CHANNEL,
        action="reload",
        issued_at=issued_at,
        issued_by="channel-automation",
        command_id=command_id,
    )


def _settle(tmp_path: Path, strategy: _BoundaryStrategy, daemon: EgressDaemon) -> None:
    status_path = tmp_path / "egress" / _CHANNEL / "reload-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps({"id": strategy.reload_ids[-1], "result": "applied"}), encoding="utf-8"
    )
    daemon.process_once(_CHANNEL)


# --------------------------------------------------------------------------
# Work item 4 (part 1): the boundary that resolves to no plan must be re-asked
# one margin past it, exactly as automation's own U63 defect-A rule does.
# --------------------------------------------------------------------------


def test_u67_boundary_resolving_to_no_plan_is_reresolved_one_margin_past(
    tmp_path: Path,
) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    airing = _plan(tmp_path, _AIRING_LABEL)
    due_next = _plan(tmp_path, _NEXT_LABEL)
    strategy = _BoundaryStrategy(tmp_path)

    # The instant automation derived and recorded: the closing item's own end.
    recorded_boundary = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=9)
    asked: list[tuple[datetime, tuple[tuple[str, float], ...]]] = []

    def wallclock_provider(channel_id: str) -> EgressSourcePlan:
        return airing

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        asked.append((boundary_at, tuple(sorted(kwargs.items()))))
        # ``build_source_plan_from_schedule`` tests each item half-open
        # (``starts_at <= t < ends_at``), so the closing item's own derived end
        # is NOT inside it and resolves to nothing -- while the instant one
        # margin past it is the next item, genuinely due.
        if boundary_at <= recorded_boundary:
            return None
        return due_next

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        encoder_strategy=strategy,
    )
    store.enqueue_command(_start_command())
    daemon.process_once(_CHANNEL)
    assert store.read_state(_CHANNEL).current_source_label == _AIRING_LABEL

    command_id = "rollover-education-0325"
    daemon.record_rollover_plan_end(
        _CHANNEL, recorded_boundary, command_id=command_id, min_plan_seconds=690.0
    )
    store.enqueue_command(_reload_command(command_id, recorded_boundary - timedelta(minutes=9)))
    daemon.process_once(_CHANNEL)

    # GREEN: the same boundary is asked twice -- the boundary itself, then one
    # margin past it -- and the plan genuinely due there is armed.
    assert [instant for instant, _ in asked] == [
        recorded_boundary,
        recorded_boundary + timedelta(seconds=1),
    ], "the boundary that resolves to no plan must be re-asked one margin past it"
    assert asked[0][1] == asked[1][1], "the second look must carry the same horizon"
    assert len(strategy.reload_requests) == 1, "no preparation was queued for the next program"
    requested = strategy.reload_requests[0]
    assert requested.source_plan.segments[0].label == _NEXT_LABEL
    assert requested.switch_at_end_of_current is True
    assert len(strategy.reload_ids) == 1 and strategy.reload_ids[0]
    # Armed, not landed: the outgoing program is still the operator-visible
    # source until the worker reports settlement.
    assert store.read_state(_CHANNEL).current_source_label == _AIRING_LABEL

    _settle(tmp_path, strategy, daemon)
    # The rollover landed the next program before the outgoing plan ended.
    assert store.read_state(_CHANNEL).current_source_label == _NEXT_LABEL


def test_u67_boundary_decline_is_named_in_the_log(tmp_path: Path, caplog) -> None:
    """The silence that cost 34 hours: a declined seamless reload with no line."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    airing = _plan(tmp_path, _AIRING_LABEL)
    strategy = _BoundaryStrategy(tmp_path)

    def wallclock_provider(channel_id: str) -> EgressSourcePlan:
        return airing

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        return None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        encoder_strategy=strategy,
    )
    store.enqueue_command(_start_command())
    daemon.process_once(_CHANNEL)

    command_id = "rollover-education-declined"
    boundary = datetime.now(UTC) + timedelta(hours=1)
    daemon.record_rollover_plan_end(_CHANNEL, boundary, command_id=command_id)
    store.enqueue_command(_reload_command(command_id, datetime.now(UTC)))
    with caplog.at_level("WARNING", logger="civiccast.egress.daemon"):
        daemon.process_once(_CHANNEL)

    # GENUINELY unplannable: no plan at the boundary, none one margin past it.
    assert strategy.reload_requests == []
    assert any("resolved no plan" in record.message for record in caplog.records), (
        "a declined seamless reload must say so; silence is what cost 34 hours"
    )
    state = store.read_state(_CHANNEL)
    assert state.state == "TRANSITIONING"
    assert state.last_error is None


# --------------------------------------------------------------------------
# Work item 3: the watchdog. Independent of the root cause, and unable to fire
# on a healthy rollover (nothing on the healthy path writes the pin).
# --------------------------------------------------------------------------


def test_u67_watchdog_bound_derivation() -> None:
    """Flat while nothing is known; stretched by a recorded horizon."""

    assert _HAS_WATCHDOG, "the U67 reload-stall watchdog is absent from this revision"
    assert _WATCHDOG_SECONDS == 1650.0  # 690s rollover lead + 960s settlement deadline
    assert _GRACE_SECONDS == 300.0
    assert _bound_seconds(None) == 1650.0
    # A horizon already past cannot shorten the bound below its floor.
    assert _bound_seconds(datetime.now(UTC) - timedelta(hours=2)) == 1650.0
    # A horizon still ahead extends it: the plan left to air, then the lead,
    # then the settle deadline -- 2h + 1650s.
    assert _bound_seconds(datetime.now(UTC) + timedelta(hours=2)) == pytest.approx(
        7200.0 + 1650.0, abs=30.0
    )


def test_u67_watchdog_reissues_then_restarts_a_pinned_channel(tmp_path: Path) -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config())
    airing = _plan(tmp_path, _AIRING_LABEL)
    strategy = _BoundaryStrategy(tmp_path)
    clock = _Clock()
    reissue_asks = {"count": 0}
    holder: dict[str, EgressDaemon] = {}

    def wallclock_provider(channel_id: str):
        # The pin is the daemon's own record that this channel's hand-off
        # failed; while it stands, the schedule has nothing to give it.
        if channel_id in holder["daemon"]._pending_reloads:
            reissue_asks["count"] += 1
            return None
        return airing

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        return None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        encoder_strategy=strategy,
        monotonic=clock,
    )
    holder["daemon"] = daemon

    store.enqueue_command(_start_command())
    daemon.process_once(_CHANNEL)
    assert store.read_state(_CHANNEL).current_source_label == _AIRING_LABEL

    # A passed boundary now requests an immediate restart. Exercise the watchdog
    # independently with a worker that ignores that first termination request.
    strategy.process.ignore_terminate = True
    boundary = datetime.now(UTC)
    command_id = "rollover-education-0325"
    daemon.record_rollover_plan_end(_CHANNEL, boundary, command_id=command_id)
    store.enqueue_command(_reload_command(command_id, boundary))
    daemon.process_once(_CHANNEL)

    # Stuck exactly as the station got stuck: pinned, nothing in flight, no plan.
    assert _CHANNEL in daemon._pending_reloads
    assert strategy.reload_requests == []
    assert store.read_state(_CHANNEL).state == "TRANSITIONING"
    assert store.read_state(_CHANNEL).pid == 24516
    assert strategy.process.terminate_requests == 1

    # Inside the bound: nothing happens (a normal ~2s tick, many times over).
    for _ in range(5):
        clock.advance(2.0)
        daemon.process_once(_CHANNEL)
    assert getattr(daemon, "_reload_stall_rungs", {}).get(_CHANNEL, 0) == 0
    assert store.read_state(_CHANNEL).state == "TRANSITIONING"

    # Past the bound: rung 1 -- ERROR and one re-issue, still pinned.
    clock.advance(_WATCHDOG_SECONDS + 1.0)
    daemon.process_once(_CHANNEL)
    assert reissue_asks["count"] == 1, "the watchdog must re-issue the rollover from here"
    assert _CHANNEL in daemon._pending_reloads
    assert getattr(daemon, "_reload_stall_rungs", {}).get(_CHANNEL, 0) == 1

    # Past the re-issue grace: rung 2 -- the pin is dropped and the worker is
    # terminated, so _poll_process's crash-relaunch owns the recovery.
    strategy.process.ignore_terminate = False
    clock.advance(_GRACE_SECONDS + 1.0)
    daemon.process_once(_CHANNEL)
    assert _CHANNEL not in daemon._pending_reloads, "a stalled channel must not stay pinned"
    assert strategy.process.returncode == 0, "the wedged worker must be terminated"

    # No human: the next tick sees the exit and relaunches a fresh plan.
    clock.advance(2.0)
    daemon.process_once(_CHANNEL)
    assert store.read_state(_CHANNEL).state == "ON_AIR"
    assert store.read_state(_CHANNEL).current_source_label == _AIRING_LABEL
    assert len(strategy.start_requests) == 2


def test_u67_rung_survives_a_failed_preparation_and_clears_on_air(tmp_path: Path) -> None:
    """MSI reload-preparation overrun (2026-11-20): the watchdog's re-issue
    declined again, this time because its preparation FAILED. Re-arming the pin
    from that failure used to clear the rung, so the watchdog re-issued forever
    at the full bound instead of reaching the restart rung, and the channel sat
    on its slate. The rung belongs to the stall episode; only the pin clearing
    (a real return to air) ends it."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    airing = _plan(tmp_path, _AIRING_LABEL)
    strategy = _BoundaryStrategy(tmp_path)
    clock = _Clock()
    holder: dict[str, EgressDaemon] = {}

    def wallclock_provider(channel_id: str):
        if channel_id in holder["daemon"]._pending_reloads:
            return None
        return airing

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        return None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        encoder_strategy=strategy,
        monotonic=clock,
    )
    holder["daemon"] = daemon

    store.enqueue_command(_start_command())
    daemon.process_once(_CHANNEL)
    strategy.process.ignore_terminate = True
    boundary = datetime.now(UTC)
    command_id = "rollover-education-0325"
    daemon.record_rollover_plan_end(_CHANNEL, boundary, command_id=command_id)
    store.enqueue_command(_reload_command(command_id, boundary))
    daemon.process_once(_CHANNEL)

    # Reach rung 1: the watchdog re-issued once, the re-issue declined, and the
    # pin was re-armed.
    clock.advance(_WATCHDOG_SECONDS + 1.0)
    daemon.process_once(_CHANNEL)
    assert daemon._reload_stall_rungs.get(_CHANNEL) == 1
    assert _CHANNEL in daemon._pending_reloads

    # The re-issued reload's preparation now FAILS and falls back to restart,
    # which re-arms the SAME pin through ``_arm_pending_reload``.
    daemon._failed_reload_preparations[_CHANNEL] = (
        daemon._monotonic(),
        boundary,
    )
    daemon._arm_pending_reload(_CHANNEL, store.read_state(_CHANNEL), plan_end_at=None)
    assert daemon._reload_stall_rungs.get(_CHANNEL) == 1, (
        "a failed preparation is the same stall episode; it must not reset the rung"
    )

    # One grace past that re-arm: rung 2 -- the restart, not another re-issue.
    strategy.process.ignore_terminate = False
    clock.advance(_GRACE_SECONDS + 1.0)
    daemon.process_once(_CHANNEL)
    assert _CHANNEL not in daemon._pending_reloads, "the stalled channel must not re-issue forever"
    assert strategy.process.returncode == 0

    # Back on air: the pin and the episode's bookkeeping are gone.
    clock.advance(2.0)
    daemon.process_once(_CHANNEL)
    assert store.read_state(_CHANNEL).state == "ON_AIR"
    assert daemon._reload_stall_rungs == {}


def test_u67_real_sequence_reaches_the_restart_after_a_failed_preparation(
    tmp_path: Path,
) -> None:
    """MSI reload-preparation overrun (2026-11-20), the REAL flow.

    Rung-1 expiry re-issues the reload; that re-issue starts an ASYNCHRONOUS
    preparation, so ``has_pending_reload_settlement`` is true and the next
    watchdog tick takes the settlement branch. That branch used to POP the
    rung, so when the preparation then FAILED and re-armed the pin, the episode
    was back on rung 0: the watchdog re-issued again at the full bound and the
    channel sat on its slate instead of ever reaching the restart."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    airing = _plan(tmp_path, _AIRING_LABEL)
    strategy = _BoundaryStrategy(tmp_path)
    clock = _Clock()
    gate = threading.Event()
    holder: dict[str, EgressDaemon] = {}

    prep_calls = {"count": 0}

    def sync_preparer(plan, config):  # pragma: no cover - the async path must win
        raise AssertionError("the async preparer must be used")

    def async_preparer(plan, config, cancel, protected):
        prep_calls["count"] += 1
        if prep_calls["count"] == 1:
            # The channel START's own preparation: let the worker come up.
            return SourcePreparationReport(plan, ())
        # Every later preparation is the re-issue's, and it FAILS.
        gate.wait(timeout=10)
        raise SourcePrepareError("prepared media failed")

    def wallclock_provider(channel_id: str):
        # The re-issue must reach preparation this time, so it resolves a plan.
        return airing

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        return None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        encoder_strategy=strategy,
        source_preparer=sync_preparer,
        async_source_preparer=async_preparer,
        monotonic=clock,
    )
    holder["daemon"] = daemon
    daemon.enable_async_preparation()
    try:
        store.enqueue_command(_start_command())
        # The start's own preparation runs on the executor. Wait for that
        # FUTURE to finish (the real condition), then let the daemon settle it
        # and launch the worker.
        daemon.process_once(_CHANNEL)
        pending_start = daemon._preparations.get(_CHANNEL)
        if pending_start is not None:
            pending_start.future.result(timeout=10)
            daemon.process_once(_CHANNEL)
        assert daemon._processes.get(_CHANNEL) is not None
        strategy.process.ignore_terminate = True
        boundary = datetime.now(UTC)
        command_id = "rollover-education-0325"
        daemon.record_rollover_plan_end(_CHANNEL, boundary, command_id=command_id)
        store.enqueue_command(_reload_command(command_id, boundary))
        daemon.process_once(_CHANNEL)
        assert _CHANNEL in daemon._pending_reloads

        # Rung 1: the expiry re-issues, and the re-issue's preparation is now
        # running in the background.
        clock.advance(_WATCHDOG_SECONDS + 1.0)
        daemon.process_once(_CHANNEL)
        assert daemon._reload_stall_rungs.get(_CHANNEL) == 1
        assert _CHANNEL in daemon._preparations, "the re-issue's preparation must be in flight"
        assert daemon.has_pending_reload_settlement(_CHANNEL)

        # A tick WITH the preparation in flight takes the settlement branch. It
        # resets the clock but must keep the rung: this is the same stall.
        clock.advance(2.0)
        daemon.process_once(_CHANNEL)
        assert _CHANNEL in daemon._preparations
        assert daemon._reload_stall_rungs.get(_CHANNEL) == 1, (
            "a re-issue whose preparation is still in flight is the SAME stall"
        )

        # The preparation FAILS: ``_settle_preparation`` falls back to restart
        # and re-arms the pin through ``_arm_pending_reload``. Rung 1 survives.
        pending = daemon._preparations[_CHANNEL]
        gate.set()
        # Wait for the background job to actually finish (with its error)
        # before ticking. Otherwise ``process_once`` can run while the future
        # is still pending, the daemon correctly keeps the preparation, and
        # the assert below races the executor.
        pending.future.exception(timeout=10)
        clock.advance(2.0)
        daemon.process_once(_CHANNEL)
        assert _CHANNEL not in daemon._preparations
        assert _CHANNEL in daemon._pending_reloads
        assert daemon._reload_stall_rungs.get(_CHANNEL) == 1, (
            "a failed preparation after a re-issue is still the same stall"
        )

        # One re-issue grace past the re-arm: rung 2 -- the restart, not
        # another re-issue at the full bound.
        strategy.process.ignore_terminate = False
        clock.advance(_GRACE_SECONDS + 1.0)
        daemon.process_once(_CHANNEL)
        assert _CHANNEL not in daemon._pending_reloads, (
            "the channel must reach the restart instead of re-issuing forever"
        )
        assert strategy.process.returncode == 0, "the wedged worker must be terminated"
    finally:
        gate.set()
        daemon.shutdown_preparation()


def test_u67_a_new_stall_after_the_pin_cleared_starts_at_rung_zero(tmp_path: Path) -> None:
    """The rung belongs to ONE stall episode. Once the pin is really gone the
    watchdog clears the rung, so a stall that arms afterwards gets the full
    bound and a fresh re-issue rather than an immediate restart."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    airing = _plan(tmp_path, _AIRING_LABEL)
    strategy = _BoundaryStrategy(tmp_path)
    clock = _Clock()
    reissue_asks = {"count": 0}
    holder: dict[str, EgressDaemon] = {}

    def wallclock_provider(channel_id: str):
        if channel_id in holder["daemon"]._pending_reloads:
            reissue_asks["count"] += 1
            return None
        return airing

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        return None

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        encoder_strategy=strategy,
        monotonic=clock,
    )
    holder["daemon"] = daemon

    store.enqueue_command(_start_command())
    daemon.process_once(_CHANNEL)
    strategy.process.ignore_terminate = True
    boundary = datetime.now(UTC)
    command_id = "rollover-education-0325"
    daemon.record_rollover_plan_end(_CHANNEL, boundary, command_id=command_id)
    store.enqueue_command(_reload_command(command_id, boundary))
    daemon.process_once(_CHANNEL)

    # Reach rung 1.
    clock.advance(_WATCHDOG_SECONDS + 1.0)
    daemon.process_once(_CHANNEL)
    assert daemon._reload_stall_rungs.get(_CHANNEL) == 1

    # The episode ends: the pin is gone by the time the watchdog next looks.
    daemon._pending_reloads.pop(_CHANNEL, None)
    clock.advance(2.0)
    daemon.process_once(_CHANNEL)
    assert daemon._reload_stall_rungs == {}, "the rung must not outlive its stall episode"

    # A NEW stall now arms. At its expiry it must be treated as rung 0: the
    # full bound, then one re-issue -- not an immediate restart.
    asks_before = reissue_asks["count"]
    daemon._arm_pending_reload(_CHANNEL, store.read_state(_CHANNEL), plan_end_at=None)
    clock.advance(_WATCHDOG_SECONDS + 1.0)
    daemon.process_once(_CHANNEL)
    assert reissue_asks["count"] == asks_before + 1, "the new stall must re-issue, not restart"
    assert daemon._reload_stall_rungs.get(_CHANNEL) == 1
    assert strategy.process.returncode is None, "a new stall starts at rung 0, not at the restart"


def test_u67_a_healthy_rollover_never_reaches_the_watchdog(tmp_path: Path) -> None:
    """The pin is only ever armed on a FAILED hand-off, so a normal rollover --
    including one whose plan is shorter than the lead -- is untouched."""

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    airing = _plan(tmp_path, _AIRING_LABEL)
    due_next = _plan(tmp_path, _NEXT_LABEL)
    strategy = _BoundaryStrategy(tmp_path)
    clock = _Clock()

    def wallclock_provider(channel_id: str) -> EgressSourcePlan:
        return airing

    def boundary_provider(channel_id: str, boundary_at: datetime, **kwargs: float):
        return due_next

    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=wallclock_provider,
        boundary_source_plan_provider=boundary_provider,
        encoder_strategy=strategy,
        monotonic=clock,
    )
    store.enqueue_command(_start_command())
    daemon.process_once(_CHANNEL)

    command_id = "rollover-education-ordinary"
    boundary = datetime.now(UTC) + timedelta(seconds=554)
    daemon.record_rollover_plan_end(
        _CHANNEL, boundary, command_id=command_id, min_plan_seconds=690.0
    )
    store.enqueue_command(_reload_command(command_id, datetime.now(UTC)))
    daemon.process_once(_CHANNEL)
    assert len(strategy.reload_requests) == 1

    # The hand-off lands, as it does on every ordinary boundary.
    _settle(tmp_path, strategy, daemon)
    assert store.read_state(_CHANNEL).current_source_label == _NEXT_LABEL

    # A day of ordinary ticks -- far past every bound -- and the watchdog never
    # has anything to say, because no pin was ever armed.
    for _ in range(200):
        clock.advance(690.0)
        daemon.process_once(_CHANNEL)
    assert getattr(daemon, "_reload_stall_rungs", {}) == {}
    assert getattr(daemon, "_reload_stall_since", {}) == {}
    assert len(strategy.reload_requests) == 1, "no re-issue was ever needed"
    assert store.read_state(_CHANNEL).current_source_label == _NEXT_LABEL
