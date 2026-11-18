# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""U60: a plan SHORTER than the rollover lead strands its successor's preparation.

Live C10, 2026-09-27 21:17:42. The station rolls one scheduled item per plan
(``civiccast/egress/source_plan.py``: ``max_segments=1`` on the GStreamer path),
so the plan on air is one item long. The rollover trigger is

    ``max(last_segment_start_at, plan_end_at - lead)``

and for an item shorter than ``lead`` (690s at the station's 300s preparation
timeout -- see ``_rollover_lead_seconds``) the first term wins: the dispatch for
the NEXT boundary fires the instant the CURRENT plan settles, which is also the
instant that dispatch's own synchronous cold conform starts. The preparation's
whole runway is therefore the length of the plan on air -- measured live at 290s
for a government item whose first-time conform took 383.6s. The plan ran to EOS
with no reload in flight, the worker held the slate for ~100s, and the next
dispatch inherited the delay: a latched lateness cascade.

The fix is an off-air LOOK-AHEAD warm. On EVERY dispatch, the automation walks
the boundaries ahead and asks the daemon to warm the first one whose own
dispatch is still a full lead away -- so the cache entry the air path will need
is already resident when the reload's synchronous prepare runs. A boundary
whose dispatch is nearer than ``now + lead`` is deliberately NOT warmed: it
cannot finish in time, and warming it would run a second whole-asset conform
concurrently with the reload's own cold conform. U63 preserves one
collapsed-boundary case: when the next item's dispatch collapses onto the
boundary being dispatched now, the daemon warms it immediately because there
is no earlier dispatch opportunity.

BETA.10 U61 (2026-09-27): the look-ahead was first gated to the recovery regime
alone -- a leg SHORTER than the lead -- and that excluded the ordinary cadence
case, which is where a long title needs it most. Live: government aired a 9020s
meeting (Sustainability Advisory Board - April 2026) as 1800s slices. 1800s of
plan against a 690s lead is NOT ``inside_lead_recovery``, so the look-ahead
never ran once -- zero look-ahead lines in any station log, no ``warm/`` scratch
under any channel -- and every slice paid its own cold bounded conform (383.6s /
406.0s / 458.5s / 293.6s / 267.0s). The whole-asset conform that would have made
every slice after the first a stream-copy HIT is exactly what the walk asks for,
and in this regime the walk never ran at all. The gate is now
``lead_seconds is not None`` alone,
and the walk's own ``dispatch_at >= now + lead`` test is what keeps a racing
boundary out, in this regime exactly as in the recovery one.

This test drives ``ChannelAutomationService`` directly, mirroring
``tests/egress/test_automation_rollover_retry_log_cadence.py``'s harness. No
station is touched and the engine's changeover code is not exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from civiccast.egress.automation import ChannelAutomationService, ChannelAutomationSettings
from civiccast.egress.errors import SourcePrepareError
from civiccast.egress.models import (
    CanonicalProfile,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
    EgressStateRow,
)
from civiccast.egress.store import InMemoryEgressStore

# The station's computed lead is 690s (2 passes over its 300s preparation
# timeout + the settle budget + the margin, floored at
# ``_ROLLOVER_MIN_LEAD_SECONDS``) -- the numbers below are all relative to it.
_NOW = datetime(2026, 9, 27, 21, 17, 42, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return _NOW + timedelta(seconds=seconds)


class _FakeDaemon:
    """The cadence test's double, WITHOUT ``warm_source_plan``.

    The capability probe is the point: a daemon that cannot warm must still
    dispatch exactly as it does today.
    """

    def __init__(self, *, live_channels: set[str]) -> None:
        self.live_channels = live_channels
        self.processed: list[str] = []

    def has_live_process(self, channel_id: str) -> bool:
        return channel_id in self.live_channels

    def has_manual_override(self, channel_id: str) -> bool:
        return False

    def process_once(self, channel_id: str) -> int:
        self.processed.append(channel_id)
        return 0


class _WarmFakeDaemon(_FakeDaemon):
    """Adds the warm capability the automation looks for."""

    def __init__(self, *, live_channels: set[str], warmer=None) -> None:
        super().__init__(live_channels=live_channels)
        self.warmed: list[tuple[str, EgressSourcePlan]] = []
        self.warm_raised: list[str] = []
        self._warmer = warmer

    def warm_source_plan(self, channel_id: str, plan: EgressSourcePlan) -> None:
        self.warmed.append((channel_id, plan))
        if self._warmer is not None:
            # Recorded BEFORE the call so the list still names the channel
            # when the warmer raises out of here -- the fault has to travel
            # back through the automation's own guard, which is the thing
            # under test in ``test_a_raising_warmer_never_breaks_the_dispatch``.
            self.warm_raised.append(channel_id)
            self._warmer(channel_id, plan)


def _config(channel_id: str) -> EgressConfig:
    return EgressConfig(
        channel_id=channel_id,
        enabled=True,
        auto_start=False,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri=f"build/{channel_id}.ts")],
    )


def _item(channel_id: str, index: int, seconds: float) -> EgressSourcePlan:
    """The schedule item airing in ``[index*seconds, (index+1)*seconds)``."""

    return EgressSourcePlan(
        channel_id=channel_id,
        segments=[
            EgressSourceSegment(
                label=f"Item {index + 1}",
                path=f"C:/media/item-{index + 1}.ts",
                duration_seconds=seconds,
                kind="program",
                source_ref=f"item-{index + 1}",
            )
        ],
    )


class _Schedule:
    """A boundary provider over a finite list of equal-length items.

    ``plan_at(channel, at)`` answers the item airing at ``at`` -- the contract
    ``SourcePreparer.plan_at`` has -- and records every boundary it is asked
    about, so a test can pin which boundaries the look-ahead walked.
    """

    def __init__(self, channel_id: str, *, count: int, seconds: float) -> None:
        self.channel_id = channel_id
        self.count = count
        self.seconds = seconds
        self.calls: list[datetime] = []

    def _index(self, at: datetime) -> int:
        return int((at - _NOW).total_seconds() // self.seconds)

    def plan_at(self, channel_id: str, at: datetime) -> EgressSourcePlan | None:
        self.calls.append(at)
        index = self._index(at)
        if index < 0 or index >= self.count:
            return None
        return _item(channel_id, index, self.seconds)

    def horizon_plan(self, channel_id: str) -> EgressSourcePlan:
        """The plan the first tick establishes its horizon from."""

        return _item(channel_id, 0, self.seconds)


def _write_on_air_state(
    store: InMemoryEgressStore, channel_id: str, *, proof_event_id: str, pid: int
) -> None:
    store.write_state(
        EgressStateRow(
            channel_id=channel_id,
            state="ON_AIR",
            current_source_label="Council Meeting",
            current_proof_event_id=proof_event_id,
            updated_at=_NOW,
            pid=pid,
        )
    )


def _pending_actions(store: InMemoryEgressStore, channel_id: str) -> list[str]:
    return [command.action for command in store.pop_pending_commands(channel_id)]


def _service(
    store: InMemoryEgressStore, daemon: object, schedule: _Schedule, clock: dict[str, float]
) -> ChannelAutomationService:
    return ChannelAutomationService(
        store,
        daemon,  # type: ignore[arg-type]
        schedule.horizon_plan,
        settings=ChannelAutomationSettings(),
        boundary_source_plan_provider=schedule.plan_at,
        monotonic=lambda: clock["t"],
    )


def _armed_service(
    *, daemon: object, schedule: _Schedule, clock: dict[str, float]
) -> tuple[InMemoryEgressStore, ChannelAutomationService]:
    """Store + service with the horizon established and the first dispatch pending.

    Tick 1 (``_NOW``) establishes the tracked horizon and returns; tick 2 (two
    seconds later) is the recovery dispatch this item is about -- the plan on
    air is shorter than the lead, so the trigger collapsed to the plan's own
    start and there is nothing earlier to wait for.
    """

    store = InMemoryEgressStore()
    store.upsert_config(_config("public"))
    _write_on_air_state(store, "public", proof_event_id="ev-1", pid=100)
    service = _service(store, daemon, schedule, clock)
    service.run_once(now=_NOW)
    return store, service


def test_short_leg_lookahead_warms_the_collapsed_boundary_immediately() -> None:
    """A 290s leg is shorter than the 690s lead, so its successor collapses.

    U63 warms that successor at the current dispatch because the rollover
    trigger has no earlier slot. Walking farther would skip the item whose
    dispatch has already collapsed onto the boundary being dispatched.
    """

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=10, seconds=290.0)
    daemon = _WarmFakeDaemon(live_channels={"public"})
    store, service = _armed_service(daemon=daemon, schedule=schedule, clock=clock)

    service.run_once(now=_at(2))

    assert _pending_actions(store, "public") == ["reload"]
    assert len(daemon.warmed) == 1, daemon.warmed
    channel_id, warmed_plan = daemon.warmed[0]
    assert channel_id == "public"
    assert [seg.source_ref for seg in warmed_plan.segments] == ["item-3"]
    # The dispatch's boundary, then the first collapsed successor.
    assert schedule.calls == [_at(290), _at(580)]


def test_lookahead_warms_the_next_boundary_in_ordinary_cadence() -> None:
    """A 1800s plan under a 690s lead is NOT recovery -- and must still warm.

    U61: ``inside_lead_recovery`` is False here (an 1800s plan exceeds the 690s
    lead), which is exactly the regime the look-ahead used to skip. It is also
    the regime a long title aired as repeated 1800s slices lives in, and there
    the difference is WHEN the warm starts: queued here, at the dispatch tick,
    it gets the whole runway, while the air path's own request for the same key
    only leaves ``_prepare_segment`` at the end of that slice's conform -- a
    measured 267-458s later on 2026-09-27. A warm asked for late enough to lose
    the race leaves the next slice cold, which is what the station aired.
    """

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=4, seconds=1800.0)
    daemon = _WarmFakeDaemon(live_channels={"public"})
    store, service = _armed_service(daemon=daemon, schedule=schedule, clock=clock)

    # The boundary-aligned trigger for an 1800s plan under a 690s lead.
    service.run_once(now=_at(1120))

    assert _pending_actions(store, "public") == ["reload"]
    # Two warms, and the first is the improvement: the horizon walk warmed
    # item-2 at the tick item-1 took air (1900s of runway before its own
    # dispatch), and this dispatch's walk warmed item-3.
    channel_ids = [entry[0] for entry in daemon.warmed]
    assert channel_ids == ["public", "public"]
    # NOT item-2 at the DISPATCH: that is the plan this very reload is about
    # to arm, and warming it here would put a second whole-asset conform
    # alongside the reload's own. The walk steps to the boundary after it.
    assert [entry[1].segments[0].source_ref for entry in daemon.warmed] == [
        "item-2",
        "item-3",
    ]
    # The horizon walk's own boundary, then the dispatch's boundary and the one
    # its walk warmed.
    assert schedule.calls == [_at(1800), _at(1800), _at(3600)]


def test_the_ordinary_cadence_warm_gets_more_runway_than_the_asset_costs() -> None:
    """U61's whole claim: the warm must finish before the boundary it serves.

    Two bounds, both about the largest whole-asset conform this box has staged
    (public's 15949.199933s City Council, ``757519c3...``):

    * ``_CONFORM_SECONDS_AT_BENCH_RATE`` -- that asset divided by the standalone
      16.3x realtime recorded at ``preparer.py:96``: 978s.
    * ``_CONFORM_SECONDS_LIVE_LARGEST_MEASURED`` -- the same conform's ACTUAL
      duration on the station, which is the number the deployed station pays and
      the tighter of the two.

    The live number is readable off the filesystem and nowhere else, because the
    warm path logs nothing on success. It works because
    ``_promote_conform_into_cache`` does ``tmp.replace(final)`` -- a RENAME into
    the cache, not a copy (``preparer.py:1240``, whose docstring calls itself
    "the SOLE place a ``.ts`` is ever renamed into the persistent cache"). A
    within-volume rename preserves NTFS creation time, so a cache ``.ts`` is
    born when the conform's OUTPUT was created and its sidecar ``.json`` is born
    when the meta is written straight after the rename: the birth-to-birth gap
    IS the whole-asset conform, and a hit's ``os.utime`` refresh shows up as a
    LATER ``.ts`` mtime than ``.json`` birth. Read that way, the box measured:

    * City Council Aug 11 ``757519c3``: 21:47:44.434 -> 22:04:47.236 over
      15949.199933s = 1022.8s = 15.6x
    * Sustainability ``8564fe6c``: 23:01:45.660 -> 23:10:37.863 over
      9020.834s = 532.2s = 17.0x
    * NSF Day After Tomorrow ``e6b580bf``: 22:15:31.457 -> 22:22:18.293 over
      5334.814s = 406.8s = 13.1x

    So the live whole-asset rate is 13.1-17.0x -- essentially the bench rate, not
    slower -- and the largest whole-asset conform this box has ever been measured
    staging is 1022.8s, against the 16.3x prediction of 978s for the same asset.
    (The only larger gap on the box, ``80cdcd4c``'s 110.6s, is a probe-only
    sidecar -- ``full_asset_conform: false``, ``media_duration_seconds: null`` --
    and is not a whole-asset conform at all.)

    The runway from this dispatch to the warmed boundary's OWN dispatch has to
    cover that. The air-path warm is the SAME job asked for later: it leaves
    ``_prepare_segment`` only once that slice's own conform has finished, so its
    window is this runway minus one slice conform (measured 267-458s tonight),
    and on a single starved FIFO it was still racing when the next slice's cold
    conform took the box.

    What this does NOT prove: that the warm is always SCHEDULED early enough to
    spend this runway. The runway itself covers the conform with room to spare
    (1790s against 1023s measured; the break-even is an asset of roughly 8h,
    longer than anything that has aired here). What ate the runway live was the
    SCHEDULING -- the gate never fired (no warm was ever asked for at a dispatch
    tick) and the one global FIFO plus a daemon restart delayed the entry by
    ~100 minutes. That part is the next lever and is not fixed here; see
    ``reports/U61.md``.
    """

    _CONFORM_SECONDS_AT_BENCH_RATE = 15949.199933 / 16.3  # preparer.py:96
    _CONFORM_SECONDS_LIVE_LARGEST_MEASURED = 1022.8  # 757519c3, live
    _LEAD_SECONDS = 690.0  # the deployed lead for an 1800s item

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=4, seconds=1800.0)
    daemon = _WarmFakeDaemon(live_channels={"public"})
    _store, service = _armed_service(daemon=daemon, schedule=schedule, clock=clock)

    service.run_once(now=_at(1120))  # the dispatch; the warm is queued here

    # The last warm is item-3, the one this dispatch's walk queued. (The first
    # one is item-2, warmed by the horizon walk the moment item-1 took air --
    # see ``_warm_next_boundary_ahead`` -- which only gives it MORE runway.)
    assert [seg.source_ref for seg in daemon.warmed[0][1].segments] == ["item-2"]
    _channel_id, warmed_plan = daemon.warmed[-1]
    assert [seg.source_ref for seg in warmed_plan.segments] == ["item-3"]
    # item-3 airs at _at(3600); its own dispatch fires one lead before that.
    warmed_dispatch_at = _at(3600) - timedelta(seconds=_LEAD_SECONDS)
    runway = (warmed_dispatch_at - _at(1120)).total_seconds()
    assert runway >= _CONFORM_SECONDS_AT_BENCH_RATE
    assert runway >= _CONFORM_SECONDS_LIVE_LARGEST_MEASURED


def test_lookahead_warms_the_collapsed_boundary_when_items_are_very_short() -> None:
    """30s items: U63 still warms the successor whose dispatch has collapsed."""

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=100, seconds=30.0)
    daemon = _WarmFakeDaemon(live_channels={"public"})
    store, service = _armed_service(daemon=daemon, schedule=schedule, clock=clock)

    service.run_once(now=_at(2))

    assert _pending_actions(store, "public") == ["reload"]
    assert [seg.source_ref for seg in daemon.warmed[0][1].segments] == ["item-3"]
    assert schedule.calls == [_at(30), _at(60)]


def test_lookahead_stops_when_the_schedule_resolves_no_further_item() -> None:
    clock = {"t": 0.0}
    schedule = _Schedule("public", count=2, seconds=290.0)
    daemon = _WarmFakeDaemon(live_channels={"public"})
    store, service = _armed_service(daemon=daemon, schedule=schedule, clock=clock)

    service.run_once(now=_at(2))

    assert _pending_actions(store, "public") == ["reload"]
    assert daemon.warmed == []
    assert schedule.calls == [_at(290), _at(580)]


def test_lookahead_survives_a_provider_that_raises_mid_walk() -> None:
    """``plan_at`` raises ``SourcePrepareError`` past the last published item.

    The dispatch has already been enqueued by then; a walk that cannot resolve
    a boundary must end quietly, not break the tick.
    """

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=3, seconds=290.0)

    def raising(channel_id: str, at: datetime) -> EgressSourcePlan | None:
        if at >= _at(580):
            schedule.calls.append(at)
            raise SourcePrepareError("no schedule item at this boundary")
        return schedule.plan_at(channel_id, at)

    daemon = _WarmFakeDaemon(live_channels={"public"})
    store = InMemoryEgressStore()
    store.upsert_config(_config("public"))
    _write_on_air_state(store, "public", proof_event_id="ev-1", pid=100)
    service = ChannelAutomationService(
        store,
        daemon,  # type: ignore[arg-type]
        schedule.horizon_plan,
        settings=ChannelAutomationSettings(),
        boundary_source_plan_provider=raising,
        monotonic=lambda: clock["t"],
    )
    service.run_once(now=_NOW)

    service.run_once(now=_at(2))

    assert _pending_actions(store, "public") == ["reload"]
    assert daemon.warmed == []


def test_lookahead_is_skipped_when_the_daemon_cannot_warm() -> None:
    """The capability probe: an old daemon (no ``warm_source_plan``) is unchanged."""

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=10, seconds=290.0)
    daemon = _FakeDaemon(live_channels={"public"})
    store, service = _armed_service(daemon=daemon, schedule=schedule, clock=clock)

    service.run_once(now=_at(2))

    assert _pending_actions(store, "public") == ["reload"]


def test_a_raising_warmer_never_breaks_the_dispatch() -> None:
    """Best-effort: the reload is already enqueued, so a warm failure is logged."""

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=10, seconds=290.0)

    def boom(_channel_id: str, _plan: EgressSourcePlan) -> None:
        raise RuntimeError("warm backend unavailable")

    daemon = _WarmFakeDaemon(live_channels={"public"}, warmer=boom)
    store, service = _armed_service(daemon=daemon, schedule=schedule, clock=clock)

    service.run_once(now=_at(2))

    assert _pending_actions(store, "public") == ["reload"]
    assert [entry[1].segments[0].source_ref for entry in daemon.warmed] == ["item-3"]
    assert daemon.warm_raised == ["public"]


# ---------------------------------------------------------------------------
# A program must be prepared BEFORE its slot, whichever path put it on air
# ---------------------------------------------------------------------------


def _fresh_horizon_service(daemon: object, schedule: _Schedule, clock: dict[str, float]):
    store = InMemoryEgressStore()
    store.upsert_config(_config("public"))
    _write_on_air_state(store, "public", proof_event_id="ev-1", pid=100)
    return store, _service(store, daemon, schedule, clock)


def test_a_long_program_started_without_a_rollover_warms_its_successor_at_once() -> None:
    """MSI, 2026-11-17: government's 5.3-hour program, started by the operator.

    The look-ahead used to run only AFTER a rollover dispatch, so a plan that
    took air any other way -- an operator start, an auto_start recovery after
    the driver reboot, a crash relaunch -- never warmed the program behind it.
    Five hours later that program's boundary was reached with a cold cache and
    the channel aired the slate for ~25 minutes.

    The warm belongs at the moment the CURRENT program starts: that is the
    only instant that gives its successor the whole slot as runway, and it is
    reachable without any dispatch.
    """

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=4, seconds=19080.0)  # 5.3 hours
    daemon = _WarmFakeDaemon(live_channels={"public"})
    store, service = _fresh_horizon_service(daemon, schedule, clock)

    service.run_once(now=_NOW)

    # Nothing is due: the plan just took air. Nothing is reloaded either.
    assert _pending_actions(store, "public") == []
    assert [entry[1].segments[0].source_ref for entry in daemon.warmed] == ["item-2"]


def test_the_horizon_warm_is_issued_once_per_plan() -> None:
    """Every ~2s tick must not re-queue the same whole-asset conform."""

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=4, seconds=19080.0)
    daemon = _WarmFakeDaemon(live_channels={"public"})
    _store, service = _fresh_horizon_service(daemon, schedule, clock)

    service.run_once(now=_NOW)
    service.run_once(now=_at(2))
    service.run_once(now=_at(60))

    assert [entry[1].segments[0].source_ref for entry in daemon.warmed] == ["item-2"]


def test_a_plan_shorter_than_the_lead_is_left_to_the_dispatch_tick() -> None:
    """A 290s leg has no runway to spend: its dispatch fires on the next tick."""

    clock = {"t": 0.0}
    schedule = _Schedule("public", count=10, seconds=290.0)
    daemon = _WarmFakeDaemon(live_channels={"public"})
    _store, service = _fresh_horizon_service(daemon, schedule, clock)

    service.run_once(now=_NOW)

    assert daemon.warmed == []
