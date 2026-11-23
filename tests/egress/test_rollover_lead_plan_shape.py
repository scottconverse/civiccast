# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""rc.8 MSI round 5: the rollover lead must be sized from the plan the dispatch
will actually prepare.

Measured on the MSI (2026-11-21, one channel, Whistle, build ``eeb5ae3c``):
the lead log said ``lead=690s ... (105s at 12x + 120s)`` and, four minutes
later, the dispatch prepared TWO segments and took 812.3 s; the 10:42 rollover
logged ``(434s at 12x + 120s)`` and its dispatch took 880.7 s. Both channels
reached their boundary on slate.

The cause is a plan-shape disagreement, not a speed estimate:

* ``_probe_rollover_asset`` asked the boundary provider with no horizon, and
  the provider's ``min_plan_seconds`` defaults to 0.0, so with the gst engine's
  ``max_segments=1`` the answer was ONE segment -- the 105 s tail.
* ``EgressDaemon._reload_steps`` resolves the same boundary with
  ``min_plan_seconds=lead_seconds``, which keeps appending whole following
  items until the plan covers that horizon -- the 105 s tail PLUS the 1800 s
  item behind it.

These tests drive the REAL ``ScheduleSourcePlanProvider`` (the production
shape: ``max_segments=1``, ``plan_at`` as the boundary provider) so the two
plan shapes are the production ones, not a double's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from civiccast.egress.automation import ChannelAutomationService, ChannelAutomationSettings
from civiccast.egress.models import (
    CanonicalProfile,
    EgressConfig,
    EgressSinkSpec,
    EgressStateRow,
)
from civiccast.egress.source_plan import ScheduleSourcePlanProvider
from civiccast.egress.store import InMemoryEgressStore
from civiccast.schedule.models import ScheduleItemResponse, StaffAssetRow

_NOW = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
_LIVE_SECONDS = 1800.0
_TAIL = 105.0
_LONG = 1800.0


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id="public",
        enabled=True,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri="build/public.ts")],
    )


def _scheduled_item(asset_id: str, at: datetime, duration_seconds: float) -> ScheduleItemResponse:
    return ScheduleItemResponse(
        id=uuid4(),
        asset_id=asset_id,
        asset_title=asset_id,
        channel_id="public",
        mode="premiere",
        state="published",
        scheduled_at=at,
        duration_seconds=duration_seconds,
        notes=None,
        created_at=at - timedelta(days=1),
    )


def _asset(path: Path, asset_id: str, duration_seconds: float) -> StaffAssetRow:
    path.write_bytes(b"fixture")
    return StaffAssetRow(
        asset_id=asset_id,
        title=asset_id,
        state="validated",
        file_path=str(path),
        duration_seconds=duration_seconds,
        trim_in_seconds=0.0,
        trim_out_seconds=duration_seconds,
    )


class _Daemon:
    """The daemon capabilities automation probes, with the real
    ``rollover_asset_cached`` contract (per-path cache membership)."""

    def __init__(self, *, cached_paths: set[str] | None = None) -> None:
        self.cached_paths = {str(Path(path)) for path in (cached_paths or set())}
        self.recorded: list[tuple[datetime, float | None]] = []

    def has_live_process(self, channel_id: str) -> bool:
        return True

    def has_manual_override(self, channel_id: str) -> bool:
        return False

    def has_operator_stop_intent(self, channel_id: str) -> bool:
        return False

    def has_pending_reload_settlement(self, channel_id: str) -> bool:
        return False

    def process_once(self, channel_id: str) -> int:
        return 0

    def dispatched_plan_horizon(
        self, channel_id: str
    ) -> tuple[str | None, tuple[float, ...], bool] | None:
        return ("ev-1", (_LIVE_SECONDS,), False)

    def rollover_asset_cached(self, channel_id: str, source_path: object) -> bool:
        return str(Path(str(source_path))) in self.cached_paths

    def record_rollover_plan_end(
        self,
        channel_id: str,
        plan_end_at: datetime,
        *,
        command_id: str | None,
        force_fallback: bool = False,
        min_plan_seconds: float | None = None,
    ) -> None:
        self.recorded.append((plan_end_at, min_plan_seconds))


class _ProviderCalls:
    """Records what the boundary provider was asked, per call."""

    def __init__(self, provider: ScheduleSourcePlanProvider) -> None:
        self._provider = provider
        self.calls: list[tuple[datetime, float | None]] = []
        self.plans: list[tuple[float, ...]] = []

    def __call__(self, channel_id: str, boundary_at: datetime, **kwargs: Any):
        plan = self._provider.plan_at(channel_id, boundary_at, **kwargs)
        self.calls.append((boundary_at, kwargs.get("min_plan_seconds")))
        self.plans.append(() if plan is None else tuple(s.duration_seconds for s in plan.segments))
        return plan


def _service(
    tmp_path: Path,
    *,
    tail_seconds: float,
    long_seconds: float = _LONG,
    cached: tuple[str, ...] = (),
) -> tuple[ChannelAutomationService, InMemoryEgressStore, _Daemon, _ProviderCalls]:
    """One ON_AIR channel; at its boundary the schedule holds a ``tail_seconds``
    item followed by a ``long_seconds`` item -- the MSI's 105 s / 434 s tail
    shapes with the 1800 s window behind them."""

    tail_path = tmp_path / "tail.ts"
    long_path = tmp_path / "long.ts"
    tail_asset = _asset(tail_path, "tail", tail_seconds)
    long_asset = _asset(long_path, "long", long_seconds)
    items = [
        _scheduled_item("tail", _NOW + timedelta(seconds=_LIVE_SECONDS), tail_seconds),
        _scheduled_item(
            "long",
            _NOW + timedelta(seconds=_LIVE_SECONDS + tail_seconds),
            long_seconds,
        ),
    ]
    provider = ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel: items,
        asset_resolver=lambda asset_id: {"tail": tail_asset, "long": long_asset}[asset_id],
        # The gst engine's shape (automation.build_channel_automation).
        max_segments=1,
        media_duration_resolver=lambda _path: None,
    )
    calls = _ProviderCalls(provider)

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    store.write_state(
        EgressStateRow(
            channel_id="public",
            state="ON_AIR",
            current_source_label="Council Meeting",
            current_proof_event_id="ev-1",
            updated_at=_NOW,
            pid=4242,
        )
    )
    cached_paths = {
        "tail": str(tail_path),
        "long": str(long_path),
    }
    daemon = _Daemon(cached_paths={cached_paths[name] for name in cached})
    service = ChannelAutomationService(
        store,
        daemon,
        provider,
        settings=ChannelAutomationSettings(),
        boundary_source_plan_provider=calls,
    )
    return service, store, daemon, calls


def _actions(store: InMemoryEgressStore) -> list[str]:
    return [command.action for command in store.pop_pending_commands("public")]


def _end() -> datetime:
    return _NOW + timedelta(seconds=_LIVE_SECONDS)


class TestTheLeadIsSizedFromThePreparedPlanShape:
    def test_a_105s_tail_with_a_cold_1800s_item_behind_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 10:08:21 MSI shape. The dispatch prepared 105 s + 1800 s = 1905 s
        (measured 812.3 s), so the lead is 1905/2 + 120 = 1072.5 s -- not the
        690 s the one-segment probe produced."""

        monkeypatch.delenv("CIVICAST_EGRESS_ROLLOVER_LEAD_SECONDS", raising=False)
        service, store, daemon, _calls = _service(tmp_path, tail_seconds=_TAIL)

        service.run_once(now=_NOW)  # establish the horizon; ends at _NOW+1800
        service.run_once(now=_end() - timedelta(seconds=1073))
        assert _actions(store) == [], "1073 s of lead does not cover 1905 s of cold media"

        service.run_once(now=_end() - timedelta(seconds=1072))
        assert _actions(store) == ["reload"]
        assert daemon.recorded[-1][1] == pytest.approx(1072.5)

    def test_a_434s_tail_with_a_cold_1800s_item_behind_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 10:42:17 MSI shape: 434 s + 1800 s = 2234 s of cold media
        (measured 880.7 s), so the lead is 2234/2 + 120 = 1237 s."""

        monkeypatch.delenv("CIVICAST_EGRESS_ROLLOVER_LEAD_SECONDS", raising=False)
        service, store, daemon, _calls = _service(tmp_path, tail_seconds=434.0)

        service.run_once(now=_NOW)
        service.run_once(now=_end() - timedelta(seconds=1238))
        assert _actions(store) == []

        service.run_once(now=_end() - timedelta(seconds=1237))
        assert _actions(store) == ["reload"]
        assert daemon.recorded[-1][1] == pytest.approx(1237.0)

    def test_a_fully_cached_two_segment_plan_keeps_the_short_lead(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both segments are a conform-cache hit, so the cold term does not
        apply and the 690 s preparation-timeout lead stands."""

        monkeypatch.delenv("CIVICAST_EGRESS_ROLLOVER_LEAD_SECONDS", raising=False)
        service, store, daemon, _calls = _service(
            tmp_path, tail_seconds=_TAIL, cached=("tail", "long")
        )

        service.run_once(now=_NOW)
        service.run_once(now=_end() - timedelta(seconds=691))
        assert _actions(store) == []

        service.run_once(now=_end() - timedelta(seconds=690))
        assert _actions(store) == ["reload"]
        assert daemon.recorded[-1][1] == pytest.approx(690.0)

    def test_only_the_cold_segment_of_a_mixed_plan_is_counted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 105 s tail is cached, the 1800 s item is not: the lead is
        1800/2 + 120 = 1020 s, not 1905/2 + 120."""

        monkeypatch.delenv("CIVICAST_EGRESS_ROLLOVER_LEAD_SECONDS", raising=False)
        service, store, daemon, _calls = _service(tmp_path, tail_seconds=_TAIL, cached=("tail",))

        service.run_once(now=_NOW)
        service.run_once(now=_end() - timedelta(seconds=1021))
        assert _actions(store) == []

        service.run_once(now=_end() - timedelta(seconds=1020))
        assert _actions(store) == ["reload"]
        assert daemon.recorded[-1][1] == pytest.approx(1020.0)

    def test_the_probe_and_the_dispatch_resolve_the_same_plan_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The horizon the probe sized from is the horizon the daemon is told
        to build the plan with, and the provider answers both calls with the
        same segments."""

        monkeypatch.delenv("CIVICAST_EGRESS_ROLLOVER_LEAD_SECONDS", raising=False)
        service, store, daemon, calls = _service(tmp_path, tail_seconds=_TAIL)

        service.run_once(now=_NOW)
        service.run_once(now=_end() - timedelta(seconds=1072))
        assert _actions(store) == ["reload"]

        lead = daemon.recorded[-1][1]
        assert lead == pytest.approx(1072.5)
        # The probe asks with the flat floor. The plan that comes back already
        # spans past the lead it implies, so it IS the plan a larger horizon
        # would build -- no second question is needed.
        probe_indexes = [
            index for index, (at, horizon) in enumerate(calls.calls) if at == _end() and horizon
        ]
        assert [calls.calls[index][1] for index in probe_indexes] == [690.0]
        probe_shape = calls.plans[probe_indexes[-1]]
        assert probe_shape == (_TAIL, _LONG)
        assert sum(probe_shape) >= lead

        # Re-resolving at the horizon the daemon receives builds the same plan.
        dispatched = calls("public", _end(), min_plan_seconds=lead)
        assert dispatched is not None
        assert tuple(s.duration_seconds for s in dispatched.segments) == probe_shape
