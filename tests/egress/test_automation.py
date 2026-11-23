# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Channel automation driver tests (cable automation CA-2).

The driver makes playout self-driving: auto_start channels come back on air
after restarts, slate gaps re-plan when a program becomes due, and command
latches prevent storms. The daemon double records process_once calls; the
real InMemoryEgressStore carries configs, the durable command queue, and
state rows.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Literal

import pytest

import civiccast.egress.automation as automation_module
import civiccast.egress.preparer as preparer_module
from civiccast.egress.automation import ChannelAutomationService, ChannelAutomationSettings
from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.models import (
    CanonicalProfile,
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
    EgressStateRow,
)
from civiccast.egress.source_plan import SourcePrepareError
from civiccast.egress.store import InMemoryEgressStore

_NOW = datetime(2026, 6, 12, 6, 0, tzinfo=UTC)


class _FakeDaemon:
    def __init__(
        self,
        *,
        live_channels: set[str] | None = None,
        manual_override_channels: set[str] | None = None,
    ) -> None:
        self.live_channels = live_channels or set()
        self.processed: list[str] = []
        # B1 fix: mirrors PlayoutSupervisor.has_manual_override -- a channel in
        # this set stands in for an active operator live takeover or forced
        # fallback slate.
        self.manual_override_channels = manual_override_channels or set()
        self.operator_stopped_channels: set[str] = set()

    def has_live_process(self, channel_id: str) -> bool:
        return channel_id in self.live_channels

    def has_manual_override(self, channel_id: str) -> bool:
        return channel_id in self.manual_override_channels

    def has_operator_stop_intent(self, channel_id: str) -> bool:
        return channel_id in self.operator_stopped_channels

    def process_once(self, channel_id: str) -> int:
        self.processed.append(channel_id)
        return 0


def _config(channel_id: str, *, enabled: bool = True, auto_start: bool = False) -> EgressConfig:
    return EgressConfig(
        channel_id=channel_id,
        enabled=enabled,
        auto_start=auto_start,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri=f"build/{channel_id}.ts")],
    )


def _plan(channel_id: str) -> EgressSourcePlan:
    return EgressSourcePlan(
        channel_id=channel_id,
        segments=[
            EgressSourceSegment(
                label="Council Meeting",
                path="C:/media/council.ts",
                duration_seconds=1800,
                kind="program",
                source_ref="council",
            )
        ],
    )


def _pending_actions(store: InMemoryEgressStore, channel_id: str) -> list[str]:
    return [command.action for command in store.pop_pending_commands(channel_id)]


def _plan_with_duration(
    channel_id: str, duration_seconds: float, *, source_ref: str = "seg"
) -> EgressSourcePlan:
    return EgressSourcePlan(
        channel_id=channel_id,
        segments=[
            EgressSourceSegment(
                label="Program",
                path="C:/media/program.ts",
                duration_seconds=duration_seconds,
                kind="program",
                source_ref=source_ref,
            )
        ],
    )


class _PendingReloadAwareDaemon(_FakeDaemon):
    """A daemon double that, like the real ``EgressDaemon`` (F1 redesign),
    can report an armed-but-not-yet-settled reload via
    ``has_pending_reload_settlement``."""

    def __init__(self, *, live_channels: set[str] | None = None) -> None:
        super().__init__(live_channels=live_channels)
        self.pending_channels: set[str] = set()

    def has_pending_reload_settlement(self, channel_id: str) -> bool:
        return channel_id in self.pending_channels


class _HorizonAwareDaemon(_FakeDaemon):
    """A daemon double that, like the real ``EgressDaemon``, can say which source
    plan it actually DISPATCHED (``dispatched_plan_horizon``)."""

    def __init__(self, *, live_channels: set[str] | None = None) -> None:
        super().__init__(live_channels=live_channels)
        self.dispatched: dict[str, tuple[str | None, tuple[float, ...], bool]] = {}
        # Mirrors EgressDaemon.rollover_asset_cached: paths whose whole-asset
        # conform is already resident. Empty means every boundary is cold.
        self.cached_paths: set[str] = set()

    def dispatched_plan_horizon(
        self, channel_id: str
    ) -> tuple[str | None, tuple[float, ...], bool] | None:
        return self.dispatched.get(channel_id)

    def rollover_asset_cached(self, channel_id: str, source_path: object) -> bool:
        return str(Path(source_path)) in {str(Path(path)) for path in self.cached_paths}


def _plan_with_segments(
    channel_id: str, durations: tuple[float, ...], *, source_ref_prefix: str = "seg"
) -> EgressSourcePlan:
    return EgressSourcePlan(
        channel_id=channel_id,
        segments=[
            EgressSourceSegment(
                label=f"Program {n}",
                path=f"C:/media/program-{n}.ts",
                duration_seconds=duration,
                kind="program",
                source_ref=f"{source_ref_prefix}-{n}",
            )
            for n, duration in enumerate(durations)
        ],
    )


def _single_item_rollover_service(
    duration: float,
) -> tuple[ChannelAutomationService, InMemoryEgressStore, list[datetime]]:
    """One ON_AIR channel airing a single ``duration``-second segment, with a
    boundary-aware plan provider (the gst path's shape -- see
    ``build_channel_automation``'s ``plan_at`` branch)."""

    store = InMemoryEgressStore()
    store.upsert_config(_config("public"))
    store.write_state(
        EgressStateRow(
            channel_id="public",
            state="ON_AIR",
            current_source_label="Current programme",
            current_proof_event_id="ev-1",
            updated_at=_NOW,
            pid=123,
        )
    )
    daemon = _HorizonAwareDaemon(live_channels={"public"})
    daemon.dispatched["public"] = ("ev-1", (duration,), False)
    sampled: list[datetime] = []

    def next_plan(channel_id: str, boundary: datetime) -> EgressSourcePlan:
        sampled.append(boundary)
        return _plan_with_duration(channel_id, 300, source_ref="next-programme")

    service = ChannelAutomationService(
        store,
        daemon,
        lambda channel: _plan_with_duration(channel, duration),
        settings=ChannelAutomationSettings(),
        boundary_source_plan_provider=next_plan,
    )
    return service, store, sampled


def test_single_item_rollover_prepares_next_boundary_with_bounded_lead() -> None:
    """BETA.10 U02, retimed by U03. The gst path's lead is no longer a flat
    120s: it now covers BOTH bounded preparation passes of a normalized
    segment plus the reload settle budget and margin -- 2*300 + 30 + 60 = 690s
    at the shipped 300s timeout, against a 3600s plan that is longer than the
    lead. The provider probes at ``end - 691`` without dispatching, then resolves
    again and enqueues the reload at ``end - 690``. (U02's numbers here were
    391/390 for its single-timeout 390s lead.)"""

    service, store, sampled = _single_item_rollover_service(3600.0)
    end = _NOW + timedelta(seconds=3600)
    service.run_once(now=_NOW)
    service.run_once(now=end - timedelta(seconds=691))
    assert _pending_actions(store, "public") == []
    assert sampled == [end]
    service.run_once(now=end - timedelta(seconds=690))
    assert sampled == [end, end]
    assert _pending_actions(store, "public") == ["reload"]


def test_a_plan_shorter_than_the_lead_arms_at_its_own_start() -> None:
    """BETA.10 U03: a direct consequence of the longer lead, pinned so it is a
    decision and not a surprise. ``_check_plan_rollover`` arms the trigger at
    ``max(last_segment_start_at, plan_end_at - lead)``, so on a plan SHORTER
    than the lead (600s here) ``plan_end_at - 690`` is already in the past by
    the time the horizon is established and the clamp wins: the rollover arms
    at the plan's own start, on the first tick after the establishing one.
    Still bounded -- the ``_rollover_min_interval_seconds`` cadence floor
    (min(300, 600/2) = 300s here) governs how often a channel can dispatch."""

    service, store, sampled = _single_item_rollover_service(600.0)
    end = _NOW + timedelta(seconds=600)

    # The establishing tick only records the horizon; it never dispatches.
    service.run_once(now=_NOW)
    assert _pending_actions(store, "public") == []
    assert sampled == []

    # The next tick is already past ``max(_NOW, end - 690) == _NOW``.
    service.run_once(now=_NOW + timedelta(seconds=1))
    assert sampled == [end]
    assert _pending_actions(store, "public") == ["reload"]


def test_short_single_item_rollover_uses_the_whole_item_as_preparation_lead() -> None:
    store = InMemoryEgressStore()
    store.upsert_config(_config("public"))
    store.write_state(
        EgressStateRow(
            channel_id="public",
            state="ON_AIR",
            current_source_label="Current programme",
            current_proof_event_id="ev-1",
            updated_at=_NOW,
            pid=123,
        )
    )
    daemon = _HorizonAwareDaemon(live_channels={"public"})
    daemon.dispatched["public"] = ("ev-1", (30.0,), False)
    sampled: list[datetime] = []

    def next_plan(channel_id: str, boundary: datetime) -> EgressSourcePlan:
        sampled.append(boundary)
        return _plan_with_duration(channel_id, 30.0, source_ref="next-programme")

    service = ChannelAutomationService(
        store,
        daemon,
        lambda channel: _plan_with_duration(channel, 30.0),
        settings=ChannelAutomationSettings(),
        boundary_source_plan_provider=next_plan,
    )
    end = _NOW + timedelta(seconds=30)
    service.run_once(now=_NOW)  # establish the active plan's horizon
    service.run_once(now=_NOW + timedelta(seconds=2))

    assert sampled == [end]
    assert _pending_actions(store, "public") == ["reload"]


class TestGstRolloverLeadCoversPreparationTimeout:
    """BETA.10 U02. On the GStreamer path (``boundary_source_plan_provider``
    wired -- ``build_channel_automation`` wires it only when the gst engine is
    selected, automation.py's ``plan_at`` branch) the rollover lead was a flat
    120s. A cold conform of one 30-minute segment measured ~270s on the
    station and is bounded by ``CIVICAST_EGRESS_PREPARATION_TIMEOUT_SECONDS``
    (300 at the station), so a rollover issued 120s before the plan's end
    could not finish preparing before EOS: 2026-09-24 13:28:25, the public
    channel's rollover was issued with 119s left, its preparation did not
    finish, and the channel went dark at 13:30:30.

    The lead is now ``preparation_passes * preparation_timeout + reload settle
    budget + margin`` -- BETA.10 U03 multiplied the timeout by the number of
    bounded ffmpeg passes one preparation can run (two for a normalized gst
    segment: the loudnorm measurement, then the conform), so it is 2*300 + 30
    + 60 = 690s at the shipped 300s timeout rather than U02's 390s. Still
    clamped by ``last_segment_start_at`` and still overridable -- see
    ``ChannelAutomationService._rollover_lead_seconds``.
    """

    _PREP_ENV = preparer_module.PREPARATION_TIMEOUT_ENV
    _LEAD_ENV = "CIVICCAST_EGRESS_ROLLOVER_LEAD_SECONDS"

    def _service(
        self, durations: tuple[float, ...]
    ) -> tuple[ChannelAutomationService, InMemoryEgressStore, list[datetime]]:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        store.write_state(
            EgressStateRow(
                channel_id="public",
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id="ev-1",
                updated_at=_NOW,
                pid=123,
            )
        )
        daemon = _HorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", durations, False)
        boundaries: list[datetime] = []

        def next_plan(channel_id: str, boundary: datetime) -> EgressSourcePlan:
            boundaries.append(boundary)
            return _plan_with_duration(channel_id, durations[0], source_ref="next-programme")

        service = ChannelAutomationService(
            store,
            daemon,
            lambda channel: _plan_with_duration(channel, durations[0]),
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=next_plan,
        )
        return service, store, boundaries

    def test_a_300s_preparation_timeout_arms_with_200s_of_plan_left(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The station's own incident shape: a 10-minute plan, 200s still to
        run. Under the old flat 120s lead nothing was issued until 120s were
        left; now the reload is already armed. U03: this 600s plan is also
        shorter than the new 690s lead, so the trigger is satisfied the moment
        the horizon is established -- the establishing tick itself never
        dispatches (``_check_plan_rollover`` returns after recording the
        horizon), which is what this test's first ``run_once`` sees."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        monkeypatch.setenv(self._PREP_ENV, "300")
        service, store, boundaries = self._service((600.0,))
        end = _NOW + timedelta(seconds=600)

        service.run_once(now=_NOW)  # establish the horizon (EOS = _NOW+600)
        assert boundaries == []

        service.run_once(now=end - timedelta(seconds=200))

        assert boundaries == [end]
        assert _pending_actions(store, "public") == ["reload"]

    def test_a_two_pass_preparation_is_not_covered_by_a_one_pass_lead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BETA.10 U03, RED-first for the two-pass fix. A normalized segment's
        preparation runs TWO bounded ffmpeg passes, not one -- the loudnorm
        MEASUREMENT pass (``preparer.py:1907``) and then the conform itself
        (``preparer.py:1937``) -- and ``_run_ffmpeg`` applies
        ``self._preparation_timeout_seconds`` to EACH (``preparer.py:1432``).
        At the station's 300s that is 600s of ffmpeg, so the U02 lead of
        300 + 30 + 60 = 390s did not cover it. With EOS 500s away nothing was
        armed under the old lead; the lead must now be past that point."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        monkeypatch.setenv(self._PREP_ENV, "300")
        service, store, boundaries = self._service((1800.0,))
        end = _NOW + timedelta(seconds=1800)

        service.run_once(now=_NOW)  # establish the horizon (EOS = _NOW+1800)
        service.run_once(now=end - timedelta(seconds=500))

        assert boundaries == [end]
        assert _pending_actions(store, "public") == ["reload"]

    def test_the_lead_is_the_preparation_timeout_plus_settle_and_margin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """690s = 2 x 300s preparation timeout (one loudnorm measurement pass
        plus the conform -- BETA.10 U03) + 30s reload settle budget + 60s
        margin, pinned at the trigger boundary either side of it."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        monkeypatch.setenv(self._PREP_ENV, "300")
        service, store, boundaries = self._service((1800.0,))
        # The boundary item is already cached: a cache hit keeps the short,
        # preparation-timeout-derived lead (see
        # ``test_a_cold_boundary_item_sizes_the_lead_from_the_design_speed``
        # for the cold case).
        service._daemon.cached_paths.add("C:/media/program.ts")  # type: ignore[attr-defined]
        end = _NOW + timedelta(seconds=1800)

        assert service._rollover_lead_seconds() == 690.0

        service.run_once(now=_NOW)
        service.run_once(now=end - timedelta(seconds=691))
        assert boundaries == [end]
        assert _pending_actions(store, "public") == []

        service.run_once(now=end - timedelta(seconds=690))
        assert boundaries == [end, end]
        assert _pending_actions(store, "public") == ["reload"]

    def test_a_cold_boundary_item_sizes_the_lead_from_the_design_speed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rc.8 MSI round 3 (2026-11-21): a cold 1800 s window needs the lead a
        2x realtime conform actually costs.

        The 12.0 factor assumed the station's 16.3x; the MSI measures ~2.3x, so
        a cold 1800 s window needs about 770 s and the 690 s lead dispatched it
        too late -- the channel reached its boundary on slate (measured:
        767.7 s of preparation finishing 83 s after the boundary). At the 2.0x
        design speed the lead is 1800/2 + 120 = 1020 s, and the reload is
        issued at ``end - 1020``."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        monkeypatch.setenv(self._PREP_ENV, "300")
        service, store, boundaries = self._service((1800.0,))
        end = _NOW + timedelta(seconds=1800)

        assert service._rollover_lead_seconds(1800.0) == 1020.0, (
            "a cold 1800s window must not be sized at the 12x station's rate"
        )

        service.run_once(now=_NOW)  # establish the horizon
        service.run_once(now=end - timedelta(seconds=1021))
        assert _pending_actions(store, "public") == []

        service.run_once(now=end - timedelta(seconds=1020))
        assert boundaries == [end, end]
        assert _pending_actions(store, "public") == ["reload"]

    def test_a_cached_boundary_item_keeps_the_short_lead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rc.8 MSI round 3: a cached item's preparation is a cache hit, so it
        must keep the preparation-timeout lead, not a cold conform's. Otherwise
        every ordinary rollover would arm minutes early and hold two legs."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        monkeypatch.setenv(self._PREP_ENV, "300")
        service, store, boundaries = self._service((1800.0,))
        service._daemon.cached_paths.add("C:/media/program.ts")  # type: ignore[attr-defined]
        end = _NOW + timedelta(seconds=1800)

        assert service._rollover_lead_seconds(1800.0, asset_cached=True) == 690.0

        service.run_once(now=_NOW)
        service.run_once(now=end - timedelta(seconds=691))
        assert _pending_actions(store, "public") == []

        service.run_once(now=end - timedelta(seconds=690))
        assert boundaries == [end, end]
        assert _pending_actions(store, "public") == ["reload"]

    def test_the_env_override_restores_a_shorter_lead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(self._PREP_ENV, "300")
        monkeypatch.setenv(self._LEAD_ENV, "90")
        service, store, boundaries = self._service((1800.0,))
        end = _NOW + timedelta(seconds=1800)

        assert service._rollover_lead_seconds() == 90.0

        service.run_once(now=_NOW)
        service.run_once(now=end - timedelta(seconds=91))
        assert boundaries == [end]
        assert _pending_actions(store, "public") == []

        service.run_once(now=end - timedelta(seconds=90))
        assert boundaries == [end, end]
        assert _pending_actions(store, "public") == ["reload"]

    @pytest.mark.parametrize("bad", ["abc", "-5", "0"])
    def test_an_invalid_override_warns_and_falls_back_to_the_computed_lead(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
    ) -> None:
        monkeypatch.setenv(self._PREP_ENV, "300")
        monkeypatch.setenv(self._LEAD_ENV, bad)
        service, store, boundaries = self._service((1800.0,))
        # Cached boundary item: the fallback is the timeout-derived lead.
        service._daemon.cached_paths.add("C:/media/program.ts")  # type: ignore[attr-defined]
        end = _NOW + timedelta(seconds=1800)

        with caplog.at_level(logging.WARNING, logger="civiccast.egress.automation"):
            service.run_once(now=_NOW)
            service.run_once(now=end - timedelta(seconds=691))
            assert boundaries == [end]
            assert _pending_actions(store, "public") == []

            service.run_once(now=end - timedelta(seconds=690))

        assert boundaries == [end, end]
        assert _pending_actions(store, "public") == ["reload"]
        assert any(self._LEAD_ENV in record.getMessage() for record in caplog.records)

    def test_the_last_segment_start_clamp_still_holds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dispatched plan whose LAST segment begins later than the lead
        (a 1700s penultimate segment, then a 100s one) must still trigger at
        that segment's start -- never once the wall clock passes
        ``plan_end - lead``. Checked at both the lead's boundary and the old
        flat 120s one: the latter is what the clamp protects against now that
        the lead runs past the last segment's start. U03: the lead boundary
        here is ``end - 690`` (``_NOW+1110``), still well before the last
        segment begins at ``_NOW+1700``."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        monkeypatch.setenv(self._PREP_ENV, "300")
        service, store, boundaries = self._service((1700.0, 100.0))
        end = _NOW + timedelta(seconds=1800)
        last_segment_start = _NOW + timedelta(seconds=1700)

        service.run_once(now=_NOW)  # establish: last segment begins at +1700
        # Past the lead (_NOW+1110) but before the last segment begins.
        service.run_once(now=end - timedelta(seconds=690))
        assert boundaries == [end]
        assert _pending_actions(store, "public") == []
        # Past the OLD flat lead too (_NOW+1680); still before +1700.
        service.run_once(now=end - timedelta(seconds=120))
        assert boundaries == [end]
        assert _pending_actions(store, "public") == []

        service.run_once(now=last_segment_start)
        assert boundaries == [end, end]
        assert _pending_actions(store, "public") == ["reload"]


class TestRolloverLeadDeferWatchdogCeiling:
    """BETA.10 U03: the rollover lead is bounded above by something the
    automation service does not own -- the engine's own deferred-switch
    watchdog, ``GstPlayoutEngine.defer_switch_timeout_s`` (900s default). Past
    it the watchdog FORCES the switch to the armed leg instead of waiting for
    the outgoing plan's EOS, so a lead that long stops buying what it was
    sized for. The service warns ONCE per lifetime and uses the value anyway:
    clamping an operator's explicit override would reinstate the exact failure
    the lead exists to prevent, and clamping the computed default would make
    the lead lie about the worst-case preparation it covers."""

    _PREP_ENV = preparer_module.PREPARATION_TIMEOUT_ENV
    # Read off the module rather than retyped: U03 exists because two settings
    # were silently ignored by a one-character spelling difference, and this
    # test class had exactly that typo in it while being written.
    _LEAD_ENV = automation_module.ROLLOVER_LEAD_ENV

    def _service(self) -> tuple[ChannelAutomationService, InMemoryEgressStore]:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        store.write_state(
            EgressStateRow(
                channel_id="public",
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id="ev-1",
                updated_at=_NOW,
                pid=123,
            )
        )
        daemon = _HorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", (1800.0,), False)
        service = ChannelAutomationService(
            store,
            daemon,
            lambda channel: _plan_with_duration(channel, 1800.0),
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=lambda channel, boundary: _plan_with_duration(
                channel, 1800.0
            ),
        )
        return service, store

    def test_the_watchdog_constant_matches_the_engine_default(self) -> None:
        """``civiccast.egress.gst.engine`` cannot be imported here -- it imports
        ``gi`` at module scope (verified: ``ModuleNotFoundError: No module named
        'gi'`` on this host), which is why ``automation.py`` carries
        ``_ROLLOVER_DEFER_SWITCH_TIMEOUT_SECONDS`` as a named constant instead
        of importing the real one. This pins the two equal by reading the
        engine's own source text, so the copy cannot drift silently."""

        source = (
            Path(automation_module.__file__).resolve().parent / "gst" / "engine.py"
        ).read_text(encoding="utf-8")
        match = re.search(r"defer_switch_timeout_s:\s*float\s*=\s*([0-9.]+)", source)
        assert match is not None, "engine.py no longer declares defer_switch_timeout_s"
        assert float(match.group(1)) == (
            ChannelAutomationService._ROLLOVER_DEFER_SWITCH_TIMEOUT_SECONDS
        )
        assert ChannelAutomationService._ROLLOVER_DEFER_SWITCH_TIMEOUT_SECONDS == 900.0
        assert (
            ChannelAutomationService._ROLLOVER_DEFER_SWITCH_TIMEOUT_SECONDS
            - ChannelAutomationService._ROLLOVER_LEAD_CEILING_HEADROOM_SECONDS
        ) == 840.0

    def test_the_shipped_lead_stays_under_the_ceiling(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The default configuration must not warn: 690s of lead at the shipped
        300s timeout is 150s clear of the 840s ceiling. Pinned at the ceiling
        too (prep timeout 375s gives exactly 2*375 + 90 = 840s)."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        with caplog.at_level(logging.WARNING, logger="civiccast.egress.automation"):
            monkeypatch.setenv(self._PREP_ENV, "300")
            assert self._service()[0]._rollover_lead_seconds() == 690.0

            monkeypatch.setenv(self._PREP_ENV, "375")
            assert self._service()[0]._rollover_lead_seconds() == 840.0

        assert caplog.records == []

    def test_a_computed_lead_past_the_ceiling_warns_once_per_lifetime(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Prep timeout 500s -> 2*500 + 30 + 60 = 1090s of lead, past the
        ceiling. The value is USED, not clamped, and the warning fires exactly
        once however many ticks ask for the lead (``_check_plan_rollover`` asks
        on every ~2s poll tick in production)."""

        monkeypatch.delenv(self._LEAD_ENV, raising=False)
        monkeypatch.setenv(self._PREP_ENV, "500")
        service, _store = self._service()

        with caplog.at_level(logging.WARNING, logger="civiccast.egress.automation"):
            assert service._rollover_lead_seconds() == 1090.0
            end = _NOW + timedelta(seconds=1800)
            for tick in range(5):
                service.run_once(now=end - timedelta(seconds=1000 + tick))

        warnings = [
            record for record in caplog.records if "defer-switch watchdog" in record.getMessage()
        ]
        assert len(warnings) == 1, [record.getMessage() for record in warnings]
        assert "computed default" in warnings[0].getMessage()
        assert "1090.0" in warnings[0].getMessage()

    def test_an_override_past_the_ceiling_warns_once_and_is_used(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An operator's EXPLICIT override is never silently shortened -- it is
        used as given and named in the warning, so the report says which value
        is over the watchdog."""

        monkeypatch.setenv(self._PREP_ENV, "300")
        monkeypatch.setenv(self._LEAD_ENV, "900")
        service, _store = self._service()

        with caplog.at_level(logging.WARNING, logger="civiccast.egress.automation"):
            assert service._rollover_lead_seconds() == 900.0
            assert service._rollover_lead_seconds() == 900.0
            service.run_once(now=_NOW)

        warnings = [
            record for record in caplog.records if "defer-switch watchdog" in record.getMessage()
        ]
        assert len(warnings) == 1, [record.getMessage() for record in warnings]
        assert self._LEAD_ENV in warnings[0].getMessage()

    def test_an_override_past_the_ceiling_still_arms_the_rollover(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warning, not veto: the over-long override still drives the trigger,
        so an operator who has measured their own watchdog does not lose the
        lead they asked for."""

        monkeypatch.setenv(self._PREP_ENV, "300")
        monkeypatch.setenv(self._LEAD_ENV, "900")
        service, store = self._service()
        end = _NOW + timedelta(seconds=1800)

        service.run_once(now=_NOW)
        service.run_once(now=end - timedelta(seconds=901))
        assert _pending_actions(store, "public") == []

        service.run_once(now=end - timedelta(seconds=900))
        assert _pending_actions(store, "public") == ["reload"]


class TestRelayIdentifierValidation:
    """Audit Critical (TEST-001/QA-001): the API layer must reject relay
    identifiers the relay runtime categorically rejects - otherwise a saved
    config poisons the automation pass at air time."""

    def test_whitespace_only_values_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="sdi_relay_device"):
            EgressConfig.model_validate(
                _config("public").model_dump() | {"sdi_relay_device": "   "}
            )
        with pytest.raises(ValueError, match="ndi_relay_name"):
            EgressConfig.model_validate(_config("public").model_dump() | {"ndi_relay_name": "  "})

    def test_control_characters_are_rejected(self) -> None:
        for bad in ("Deck\nLink", "Deck\rLink", "Deck\x00Link", "Deck\x01Link", "Deck\tLink"):
            with pytest.raises(ValueError, match="control"):
                EgressConfig.model_validate(
                    _config("public").model_dump() | {"sdi_relay_device": bad}
                )
            with pytest.raises(ValueError, match="control"):
                EgressConfig.model_validate(
                    _config("public").model_dump() | {"ndi_relay_name": bad}
                )

    def test_padding_is_stripped_and_clean_values_pass(self) -> None:
        config = EgressConfig.model_validate(
            _config("public").model_dump()
            | {"sdi_relay_device": "  DeckLink Mini  ", "ndi_relay_name": " CivicCast Public "}
        )
        assert config.sdi_relay_device == "DeckLink Mini"
        assert config.ndi_relay_name == "CivicCast Public"


class TestRunOnceChannelIsolation:
    """Audit Critical: one channel's relay failure must never starve the
    other channels' supervision (run_once had no per-channel isolation)."""

    def test_poisoned_channel_does_not_starve_the_fleet(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(
            _config("aaa-poisoned").model_copy(
                update={
                    "sdi_relay_device": "DeckLink",
                    "sinks": [
                        EgressSinkSpec(kind="udp-ts", label="Headend", uri="udp://127.0.0.1:23101")
                    ],
                }
            )
        )
        store.upsert_config(_config("zzz-healthy", auto_start=True))
        daemon = _FakeDaemon(live_channels={"aaa-poisoned"})

        def exploding_factory(**_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("relay construction exploded")

        service = ChannelAutomationService(
            store,
            daemon,
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            sdi_supervisor_factory=exploding_factory,
        )

        seen = service.run_once(now=_NOW)

        # Both channels were processed despite the poisoned one raising.
        assert seen == ["aaa-poisoned", "zzz-healthy"]
        assert daemon.processed == ["aaa-poisoned", "zzz-healthy"]
        # The dark auto_start channel still got its start command.
        assert "start" in _pending_actions(store, "zzz-healthy")


class TestAutoStart:
    def test_dark_auto_start_channel_gets_exactly_one_start(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        daemon = _FakeDaemon()
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)

        # The daemon consumed the start during run_once via process_once in
        # the REAL flow; with the fake daemon the command stays queued.
        assert _pending_actions(store, "public") == ["start"]
        assert daemon.processed == ["public"]

        # Latch: still dark on the next pass (fake daemon never starts it),
        # but no second start is issued.
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

    def test_operator_stop_suppresses_auto_start_but_channel_stays_supervised(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        daemon = _FakeDaemon()
        daemon.operator_stopped_channels.add("public")
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        assert service.run_once(now=_NOW) == ["public"]

        assert _pending_actions(store, "public") == []
        assert daemon.processed == ["public"]

    def test_pending_terminal_command_wins_over_same_pass_auto_start(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        store.enqueue_command(
            EgressCommand(
                channel_id="public",
                action="stop",
                issued_at=_NOW,
                issued_by="operator",
                command_id="operator-stop",
            )
        )
        daemon = _FakeDaemon()
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)

        assert _pending_actions(store, "public") == ["stop"]
        assert daemon.processed == ["public"]

    def test_live_channel_gets_no_start_and_latch_resets_after_drop(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        daemon = _FakeDaemon(live_channels={"public"})
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

        # Encoder dies (operatorless drop) -> next pass re-issues start.
        daemon.live_channels.clear()
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["start"]

    def test_non_auto_start_and_disabled_channels_are_left_alone(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("manual", auto_start=False))
        store.upsert_config(_config("dark", enabled=False, auto_start=True))
        daemon = _FakeDaemon()
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        seen = service.run_once(now=_NOW)

        assert seen == ["manual"]
        assert _pending_actions(store, "manual") == []
        assert _pending_actions(store, "dark") == []
        assert daemon.processed == ["manual"]

    @pytest.mark.parametrize("terminal_action", ["stop", "drain"])
    def test_disabled_channel_drains_terminal_command_without_executing_start(
        self, tmp_path: Path, terminal_action: Literal["stop", "drain"]
    ) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("disabled", enabled=False, auto_start=True))
        store.write_state(EgressStateRow(channel_id="disabled", state="ON_AIR", updated_at=_NOW))
        store.enqueue_command(
            EgressCommand(
                channel_id="disabled",
                action="start",
                issued_at=_NOW,
                issued_by="operator",
                command_id="start-before-disable",
            )
        )
        store.enqueue_command(
            EgressCommand(
                channel_id="disabled",
                action=terminal_action,
                issued_at=_NOW + timedelta(seconds=1),
                issued_by="operator",
                command_id=f"{terminal_action}-after-disable",
            )
        )
        start_attempts: list[str] = []

        def source_plan(channel_id: str) -> EgressSourcePlan:
            start_attempts.append(channel_id)
            raise AssertionError("disabled start reached source preparation")

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=source_plan,
        )
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        seen = service.run_once(now=_NOW)

        assert seen == []
        state = store.read_state("disabled")
        assert state is not None and state.state == "STOPPED"
        assert _pending_actions(store, "disabled") == []
        assert start_attempts == []

    def test_disabled_drain_wins_over_queued_reload_and_stays_supervised(
        self, tmp_path: Path
    ) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("disabled", enabled=False))
        store.write_state(EgressStateRow(channel_id="disabled", state="ON_AIR", updated_at=_NOW))
        store.enqueue_command(
            EgressCommand(
                channel_id="disabled",
                action="drain",
                issued_at=_NOW,
                issued_by="operator",
                command_id="drain-before-reload",
            )
        )
        store.enqueue_command(
            EgressCommand(
                channel_id="disabled",
                action="reload",
                issued_at=_NOW + timedelta(seconds=1),
                issued_by="operator",
                command_id="reload-after-drain",
            )
        )
        prepared: list[str] = []

        def source_plan(channel_id: str) -> EgressSourcePlan:
            prepared.append(channel_id)
            raise AssertionError("disabled reload reached source preparation")

        daemon = EgressDaemon(
            store,
            work_dir=tmp_path,
            source_plan_provider=source_plan,
        )

        class _LiveProcess:
            pid = 4242

            def __init__(self) -> None:
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def terminate(self) -> None:
                self.returncode = 0

        process = _LiveProcess()
        daemon._processes["disabled"] = process
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)
        state = store.read_state("disabled")
        assert state is not None and state.state == "DRAINING"
        assert _pending_actions(store, "disabled") == []
        assert prepared == []
        assert process.returncode is None

        # Disabled channels must continue supervising an already accepted
        # drain, without starting schedules or processing a rejected reload.
        service.run_once(now=_NOW + timedelta(seconds=2))
        state = store.read_state("disabled")
        assert state is not None and state.state == "DRAINING"
        assert process.returncode is None

        process.returncode = 0
        service.run_once(now=_NOW + timedelta(seconds=3))
        state = store.read_state("disabled")
        assert state is not None and state.state == "STOPPED"
        assert prepared == []


class TestSlateReplan:
    def test_upcoming_slate_program_is_prearmed_once_without_a_parallel_warm(self):
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._slate_state(store, "public")
        start = _NOW + timedelta(seconds=180)
        recorded, warmed = [], []
        clock = {"t": 0.0}

        class Provider:
            due = False

            def __call__(self, _channel_id):
                return _plan(_channel_id) if self.due else None

            def next_item_start_at(self, _channel_id, _after):
                return start

        class Daemon(_FakeDaemon):
            pending_scheduled = False

            def has_pending_scheduled_start(self, _channel_id):
                return self.pending_scheduled

            def can_prearm_slate_program(self, _channel_id):
                return True

            def record_rollover_plan_end(self, channel_id, at, **kwargs):
                recorded.append((channel_id, at, kwargs["command_id"]))

            def warm_source_plan(self, _channel_id, plan):
                warmed.append(plan)

        daemon = Daemon(live_channels={"public"})
        provider = Provider()
        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=lambda cid, at: _plan(cid) if at == start else None,
            monotonic=lambda: clock["t"],
        )
        service.run_once(now=_NOW)
        assert len(recorded) == 1
        assert recorded[0][:2] == ("public", start)
        assert recorded[0][2].startswith("auto-slate-start-")
        assert _pending_actions(store, "public") == ["reload"]
        assert warmed == []
        service.run_once(now=_NOW + timedelta(seconds=1))
        assert len(recorded) == 1
        assert _pending_actions(store, "public") == []
        daemon.pending_scheduled = True
        clock["t"] = 31.0
        service.run_once(now=_NOW + timedelta(seconds=31))
        assert len(recorded) == 1
        assert _pending_actions(store, "public") == []
        provider.due = True
        clock["t"] = 180.0
        service.run_once(now=start)
        assert _pending_actions(store, "public") == []
        # A terminal abort clears the daemon's pending entry. The due action
        # must then be allowed without having consumed an automation latch.
        daemon.pending_scheduled = False
        service.run_once(now=start)
        assert _pending_actions(store, "public") == ["reload"]

    def _slate_state(self, store: InMemoryEgressStore, channel_id: str) -> None:
        store.write_state(
            EgressStateRow(channel_id=channel_id, state="FALLBACK_SLATE", updated_at=_NOW)
        )

    def test_due_program_triggers_exactly_one_reload(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._slate_state(store, "public")
        daemon = _FakeDaemon(live_channels={"public"})
        service = ChannelAutomationService(
            store, daemon, lambda cid: _plan(cid), settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["reload"]
        # Latched while still on slate: no reload storm.
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

        # Once the channel leaves slate, the latch clears for the next gap.
        store.write_state(EgressStateRow(channel_id="public", state="ON_AIR", updated_at=_NOW))
        service.run_once(now=_NOW)
        self._slate_state(store, "public")
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["reload"]

    def test_slate_prepares_upcoming_programs_without_airing_early(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._slate_state(store, "public")
        start = _NOW + timedelta(seconds=180)
        first = _plan_with_duration("public", 75.0)
        second = _plan_with_duration("public", 90.0)
        clock = {"t": 0.0}
        calls: list[datetime] = []
        warmed: list[EgressSourcePlan] = []

        class Provider:
            due = False

            def __call__(self, _channel_id: str) -> EgressSourcePlan | None:
                return first if self.due else None

            def next_item_start_at(self, _channel_id: str, after: datetime) -> datetime:
                calls.append(after)
                return start

        class Daemon(_FakeDaemon):
            def warm_source_plan(self, _channel_id: str, plan: EgressSourcePlan) -> None:
                warmed.append(plan)

        def boundary(_channel_id: str, at: datetime) -> EgressSourcePlan | None:
            if at == start:
                return first
            if at == start + timedelta(seconds=75):
                return second
            return None

        provider = Provider()
        service = ChannelAutomationService(
            store,
            Daemon(live_channels={"public"}),
            provider,
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=boundary,
            monotonic=lambda: clock["t"],
        )
        service.run_once(now=_NOW)
        assert warmed == [first, second]
        assert _pending_actions(store, "public") == []
        clock["t"] = 2.0
        service.run_once(now=_NOW + timedelta(seconds=2))
        assert calls == [_NOW]
        # A schedule edit can be observed after the bounded warm retry interval.
        clock["t"] = 31.0
        service.run_once(now=_NOW + timedelta(seconds=31))
        assert calls == [_NOW, _NOW + timedelta(seconds=31)]
        # The warm cooldown never postpones a program that is now due.
        provider.due = True
        clock["t"] = 32.0
        service.run_once(now=start)
        assert _pending_actions(store, "public") == ["reload"]

    @pytest.mark.parametrize("failure_stage", ["lookup", "boundary", "warm"])
    def test_slate_warm_failure_does_not_block_due_reload(self, failure_stage: str) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._slate_state(store, "public")
        plan = _plan("public")

        class Provider:
            due = False

            def __call__(self, _channel_id: str) -> EgressSourcePlan | None:
                return plan if self.due else None

            def next_item_start_at(self, _channel_id: str, after: datetime) -> datetime:
                if failure_stage == "lookup":
                    raise RuntimeError("lookup unavailable")
                return after + timedelta(seconds=180)

        class Daemon(_FakeDaemon):
            def warm_source_plan(self, _channel_id: str, _plan: EgressSourcePlan) -> None:
                if failure_stage == "warm":
                    raise RuntimeError("warm queue unavailable")

        def boundary(_channel_id: str, _at: datetime) -> EgressSourcePlan:
            if failure_stage == "boundary":
                raise RuntimeError("schedule unavailable")
            return plan

        provider = Provider()
        service = ChannelAutomationService(
            store,
            Daemon(live_channels={"public"}),
            provider,
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=boundary,
        )
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []
        provider.due = True
        service.run_once(now=_NOW + timedelta(seconds=180))
        assert _pending_actions(store, "public") == ["reload"]

    @pytest.mark.parametrize("future_seconds", [None, 3600])
    def test_slate_does_not_warm_absent_or_distant_programs(self, future_seconds) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._slate_state(store, "public")
        warmed: list[EgressSourcePlan] = []

        class Provider:
            def __call__(self, _channel_id: str) -> None:
                return None

            def next_item_start_at(self, _channel_id: str, after: datetime):
                return None if future_seconds is None else after + timedelta(seconds=future_seconds)

        class Daemon(_FakeDaemon):
            def warm_source_plan(self, _channel_id: str, plan: EgressSourcePlan) -> None:
                warmed.append(plan)

        service = ChannelAutomationService(
            store,
            Daemon(live_channels={"public"}),
            Provider(),
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=lambda cid, _at: _plan(cid),
        )
        service.run_once(now=_NOW)
        assert warmed == []
        assert _pending_actions(store, "public") == []

    def test_due_program_waits_for_the_fallback_workers_first_control_connection(
        self,
    ) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._slate_state(store, "public")
        provider_calls = 0

        class _ConnectingDaemon(_FakeDaemon):
            initial_connection_observed = False

            def worker_initial_control_connection_observed(self, _channel_id: str) -> bool:
                return self.initial_connection_observed

        daemon = _ConnectingDaemon(live_channels={"public"})

        def provider(channel_id: str) -> EgressSourcePlan:
            nonlocal provider_calls
            provider_calls += 1
            return _plan(channel_id)

        service = ChannelAutomationService(
            store, daemon, provider, settings=ChannelAutomationSettings()
        )

        service._check_slate_replan("public", now=_NOW)
        assert provider_calls == 0
        assert _pending_actions(store, "public") == []
        assert "public" not in service._reload_issued
        assert "public" not in service._replan_retry_at

        daemon.initial_connection_observed = True
        service._check_slate_replan("public", now=_NOW + timedelta(seconds=1))
        assert provider_calls == 1
        assert _pending_actions(store, "public") == ["reload"]
        assert "public" in service._reload_issued
        assert "public" in service._replan_retry_at

    def test_no_plan_or_unplayable_plan_stays_on_slate(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._slate_state(store, "public")
        daemon = _FakeDaemon(live_channels={"public"})

        none_service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )
        none_service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

        def _raises(_cid: str) -> EgressSourcePlan:
            raise SourcePrepareError("media missing")

        raising_service = ChannelAutomationService(
            store, daemon, _raises, settings=ChannelAutomationSettings()
        )
        raising_service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []


class TestPlanRollover:
    """Soak evidence 2026-09-04 (kit 4b30c99, Desktop/CIVICCAST-EVIDENCE/
    soak-120-4b30c99-20260904): back-to-back scheduled premieres EOS'd the
    GStreamer worker at every source-plan boundary (6-8 restarts/channel in
    2h) because nothing ever extended a LIVE (ON_AIR) plan before it ran
    out -- only a FALLBACK_SLATE gap re-planned (TestSlateReplan above).
    ``_check_plan_rollover`` fixes this by reusing the SAME seamless
    ``reload`` dispatch _check_slate_replan already uses, before EOS."""

    def _on_air_state(
        self, store: InMemoryEgressStore, channel_id: str, *, proof_event_id: str
    ) -> None:
        store.write_state(
            EgressStateRow(
                channel_id=channel_id,
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id=proof_event_id,
                updated_at=_NOW,
            )
        )

    def test_finite_fallback_plan_rolls_over_before_eos(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        store.write_state(
            EgressStateRow(
                channel_id="public",
                state="FALLBACK_SLATE",
                current_source_label="CivicCast filler",
                current_proof_event_id="fallback-1",
                updated_at=_NOW,
            )
        )

        class _FallbackDaemon(_HorizonAwareDaemon):
            def __init__(self) -> None:
                super().__init__(live_channels={"public"})
                self.dispatched["public"] = ("fallback-1", (30.0, 30.0), False)
                self.force_fallback: list[bool] = []

            def record_rollover_plan_end(
                self,
                _channel_id: str,
                _plan_end_at: datetime,
                *,
                command_id: str | None,
                force_fallback: bool = False,
            ) -> None:
                assert command_id is not None
                self.force_fallback.append(force_fallback)

        daemon = _FallbackDaemon()
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []
        service.run_once(now=_NOW + timedelta(seconds=31))

        assert _pending_actions(store, "public") == ["reload"]
        assert daemon.force_fallback == [True]

    def test_no_rollover_while_plan_has_plenty_of_runway(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 1800.0),
            settings=ChannelAutomationSettings(),
        )

        # First tick establishes the horizon (30 min out) -- nothing due.
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []
        # A later tick, still nowhere near the end, still issues nothing.
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

    def test_rollover_dispatches_exactly_one_reload_before_eos(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        # The live plan has 40s left; the schedule underneath it extends
        # much further once the clock moves inside the lookahead window.
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store, daemon, provider, settings=ChannelAutomationSettings()
        )

        # Establish the horizon: plan ends 40s from now.
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

        # 15s later the plan is inside the 30s lookahead -> roll it over.
        later = _NOW + timedelta(seconds=15)
        service.run_once(now=later)
        assert _pending_actions(store, "public") == ["reload"]

        # Same plan boundary (proof event unchanged): no second reload while
        # the daemon has not yet applied the first one (no command storm).
        service.run_once(now=later + timedelta(seconds=1))
        assert _pending_actions(store, "public") == []

    def test_check_plan_rollover_records_the_plan_end_scoped_to_the_enqueued_commands_own_id(
        self,
    ) -> None:
        """Coordinator review, round 8, item 4: ``_check_plan_rollover``
        generates the reload command's id up front and must pass the EXACT
        same id to both ``record_rollover_plan_end`` and the ``_enqueue``
        call that dispatches it (round 7's whole point) -- nothing in the
        prior test suite pinned that the two ids actually agree, only that
        ``_enqueue`` was CALLED with *a* ``command_id`` keyword. If the two
        ever drifted apart, ``EgressDaemon._request_reload``'s command-id
        scoping would never match the command that actually drains, and the
        recorded ``plan_end_at`` would silently never be used at all --
        reintroducing dead air on an already-past horizon that should have
        cut immediately (see ``_rollover_plan_end_at``'s docstring)."""
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")

        class _RecordingDaemon(_FakeDaemon):
            def __init__(self) -> None:
                super().__init__(live_channels={"public"})
                self.recorded: list[tuple[str, datetime, str | None]] = []

            def record_rollover_plan_end(
                self,
                channel_id: str,
                plan_end_at: datetime,
                *,
                command_id: str | None,
                force_fallback: bool = False,
            ) -> None:
                self.recorded.append((channel_id, plan_end_at, command_id))

        daemon = _RecordingDaemon()
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store, daemon, provider, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)  # establish the horizon: plan ends 40s from now
        later = _NOW + timedelta(seconds=15)
        service.run_once(now=later)  # inside the lookahead -> rollover dispatch

        assert len(daemon.recorded) == 1
        pending = store.pop_pending_commands("public")
        assert [command.action for command in pending] == ["reload"]
        recorded_channel_id, _recorded_plan_end_at, recorded_command_id = daemon.recorded[0]
        assert recorded_channel_id == "public"
        # The load-bearing assertion: the id EgressDaemon will see draining
        # this command must be the exact id the plan_end was scoped to.
        assert recorded_command_id is not None
        assert recorded_command_id == pending[0].command_id

    def test_terminal_schedule_rolls_to_filler_before_eos(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")

        class _TerminalDaemon(_FakeDaemon):
            def __init__(self) -> None:
                super().__init__(live_channels={"public"})
                self.force_fallback: list[bool] = []

            def record_rollover_plan_end(
                self,
                _channel_id: str,
                _plan_end_at: datetime,
                *,
                command_id: str | None,
                force_fallback: bool = False,
            ) -> None:
                assert command_id is not None
                self.force_fallback.append(force_fallback)

        daemon = _TerminalDaemon()
        # The schedule genuinely has nothing published beyond the item
        # already airing: every call reflects the SAME fixed end time, just
        # re-anchored (join-in-progress) at whatever "now" it is asked from
        # -- exactly what build_source_plan_from_schedule does when no new
        # item follows the one currently on air.
        deadline = _NOW + timedelta(seconds=40)
        clock = {"now": _NOW}

        def provider(_cid: str) -> EgressSourcePlan:
            remaining = max(0.0, (deadline - clock["now"]).total_seconds())
            return _plan_with_duration("public", remaining)

        service = ChannelAutomationService(
            store, daemon, provider, settings=ChannelAutomationSettings()
        )

        service.run_once(now=clock["now"])
        clock["now"] = _NOW + timedelta(seconds=15)
        service.run_once(now=clock["now"])

        assert _pending_actions(store, "public") == ["reload"]
        assert daemon.force_fallback == [True]

    def test_rollover_re_establishes_horizon_once_the_reload_lands(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store, daemon, provider, settings=ChannelAutomationSettings()
        )
        service.run_once(now=_NOW)
        later = _NOW + timedelta(seconds=15)
        service.run_once(now=later)
        assert _pending_actions(store, "public") == ["reload"]

        # The daemon applies the seamless reload and writes a fresh
        # current_proof_event_id -- the tracked horizon must follow it, not
        # keep firing against the stale one.
        self._on_air_state(store, "public", proof_event_id="ev-2")
        service.run_once(now=later)
        assert _pending_actions(store, "public") == []

    def test_rollover_never_fires_off_of_the_slate_replan_state(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        store.write_state(
            EgressStateRow(channel_id="public", state="FALLBACK_SLATE", updated_at=_NOW)
        )
        daemon = _FakeDaemon(live_channels={"public"})
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 40.0),
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)
        # _check_slate_replan issues its own reload for the gap; the
        # rollover check must not ALSO fire while the channel is off ON_AIR.
        assert _pending_actions(store, "public") == ["reload"]

    # -- Hostile-review B1: skip entirely under an operator override ----------

    def test_rollover_skipped_during_live_takeover(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"}, manual_override_channels={"public"})
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 40.0),
            settings=ChannelAutomationSettings(),
        )

        # Establish, then push well past the (now much earlier) trigger point --
        # under any override the check must still never enqueue a reload.
        service.run_once(now=_NOW)
        service.run_once(now=_NOW + timedelta(seconds=35))
        assert _pending_actions(store, "public") == []

    def test_rollover_skipped_during_forced_slate(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        # The state row stays ON_AIR: a forced-slate pad toggle (swap_role)
        # writes NO state-row transition at all (B1's real defect).
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"}, manual_override_channels={"public"})
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 40.0),
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)
        service.run_once(now=_NOW + timedelta(seconds=35))
        assert _pending_actions(store, "public") == []

    def test_rollover_resumes_once_the_override_clears(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"}, manual_override_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store, daemon, provider, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)
        later = _NOW + timedelta(seconds=35)
        service.run_once(now=later)
        assert _pending_actions(store, "public") == []  # override still active

        # The override clears: the very next pass establishes a fresh horizon
        # (nothing was ever tracked while the override was active) --
        # nothing to dispatch on THIS tick yet.
        daemon.manual_override_channels.discard("public")
        service.run_once(now=later)
        assert _pending_actions(store, "public") == []

        # A subsequent pass past the boundary rolls over normally.
        service.run_once(now=later + timedelta(seconds=1))
        assert _pending_actions(store, "public") == ["reload"]

    # -- Hostile-review B2: retry pacing + a dropped reload's failure latch ----

    def test_rollover_backs_off_after_source_prepare_error(self) -> None:
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            if calls["n"] == 1:
                return _plan_with_duration("public", 40.0)
            raise SourcePrepareError("media temporarily unreadable")

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)  # establish horizon: 40s plan
        later = _NOW + timedelta(seconds=35)  # well past the boundary trigger
        service.run_once(now=later)
        assert calls["n"] == 2  # one retry attempt, which raised

        # Within the cooldown: no re-query (no command storm against a
        # persistently failing source).
        clock["now"] += 5.0
        service.run_once(now=later)
        assert calls["n"] == 2
        assert _pending_actions(store, "public") == []

        # Past the cooldown: retries.
        clock["now"] += 30.0
        service.run_once(now=later)
        assert calls["n"] == 3

    def test_rollover_retries_once_if_the_dispatched_reload_never_lands(self) -> None:
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)
        later = _NOW + timedelta(seconds=35)
        service.run_once(now=later)
        assert _pending_actions(store, "public") == ["reload"]

        # The daemon never applies it (current_proof_event_id stays ev-1 --
        # e.g. the command was dropped, or the worker control channel wasn't
        # ready). Short of the timeout: still waiting, no second reload.
        clock["now"] += 10.0
        service.run_once(now=later)
        assert _pending_actions(store, "public") == []

        # Past _ROLLOVER_ISSUED_TIMEOUT_SECONDS: the latch clears and one
        # retry is dispatched.
        clock["now"] += 40.0
        service.run_once(now=later)
        assert _pending_actions(store, "public") == ["reload"]

    def test_rollover_never_retries_while_the_daemon_reports_a_pending_settlement(
        self,
    ) -> None:
        """Hostile-review follow-up, item 2 (F1 redesign): a DEFERRED switch's
        ``current_proof_event_id`` does not change until it actually SETTLES,
        which for an automation-driven ON_AIR extension can be ~120s+ after
        this dispatches it -- far longer than ``_ROLLOVER_ISSUED_TIMEOUT_
        SECONDS`` (45s). Without checking the daemon's own "armed, still
        settling" signal first, the retry-if-undelivered branch would fire
        every ~45s while a perfectly healthy deferred reload is still
        legitimately settling (re-preparing synchronously, superseding the
        still-armed leg, creating another prepared-plan directory each time).
        Proves: zero re-dispatches across a 120s-plus wait while the daemon
        reports pending, then a normal landing once it reports settled."""
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _PendingReloadAwareDaemon(live_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)  # establish horizon: 40s plan
        later = _NOW + timedelta(seconds=35)
        service.run_once(now=later)  # trigger fires -> one reload dispatched
        assert calls["n"] == 2
        assert _pending_actions(store, "public") == ["reload"]

        # The daemon now reports this reload as armed and still settling.
        daemon.pending_channels.add("public")

        # 120 seconds pass (well past the 45s retry timeout) with the daemon
        # still reporting pending throughout -- NOT ONE re-dispatch.
        for _ in range(12):
            clock["now"] += 10.0
            service.run_once(now=later)
            assert _pending_actions(store, "public") == []
        assert calls["n"] == 2  # still just the one prepare call from the dispatch above

        # The reload finally settles: the daemon no longer reports it pending,
        # and (as the real EgressDaemon._commit_reload_settlement does)
        # current_proof_event_id changes to reflect the landed plan.
        daemon.pending_channels.discard("public")
        self._on_air_state(store, "public", proof_event_id="ev-2")
        clock["now"] += 10.0
        service.run_once(now=later)

        # Recognized as "a fresh plan just took air" (existing branch) -- the
        # horizon re-establishes from the NEW plan, no retry dispatched for
        # the now-landed one.
        assert _pending_actions(store, "public") == []

    # -- Hostile-review B3: earlier, boundary-aligned trigger ------------------

    def test_rollover_waits_for_the_last_segment_on_a_multi_segment_plan(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        # Three 10-minute segments (30 min total) already loaded -- the last
        # segment does not begin until 20 minutes in, comfortably past the
        # 120s-before-end floor (28 min in). The trigger must wait for that
        # last-segment boundary, not fire the moment the plan is established.
        plan = EgressSourcePlan(
            channel_id="public",
            segments=[
                EgressSourceSegment(
                    label=f"Program {i}",
                    path="C:/media/program.ts",
                    duration_seconds=600.0,
                    kind="program",
                    source_ref=f"seg-{i}",
                )
                for i in range(3)
            ],
        )
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return plan

        service = ChannelAutomationService(
            store, daemon, provider, settings=ChannelAutomationSettings()
        )

        service.run_once(now=_NOW)  # establish: end in 1800s, last segment at +1200s
        assert calls["n"] == 1

        # 10 minutes in -- nowhere near the last segment or the 120s floor.
        service.run_once(now=_NOW + timedelta(seconds=600))
        assert calls["n"] == 1
        assert _pending_actions(store, "public") == []

        # 19 minutes in -- still one minute short of the last-segment boundary.
        service.run_once(now=_NOW + timedelta(seconds=1140))
        assert calls["n"] == 1

        # 20 minutes in -- the last segment begins now; the trigger fires and
        # re-queries the schedule. The fake provider always returns the same
        # fixed-duration plan regardless of when it is asked (unlike the real
        # join-in-progress-aware ScheduleSourcePlanProvider), so a later query
        # necessarily projects further into the future than the original --
        # exactly the same "schedule continues further" shape the other
        # rollover tests use to trigger a dispatch. What this test actually
        # pins is the TIMING: no re-query at all before the boundary.
        service.run_once(now=_NOW + timedelta(seconds=1200))
        assert calls["n"] == 2
        assert _pending_actions(store, "public") == ["reload"]

    def test_service_wires_the_120s_lead_floor_into_rollover_trigger_at(self) -> None:
        """``_check_plan_rollover`` computes its trigger via
        ``reload_policy.rollover_trigger_at``, passing the service's
        ``_ROLLOVER_MIN_LEAD_SECONDS`` as the floor. Pinned directly against
        the (gi-free, independently unit-tested) pure function rather than
        end-to-end, since a plan with a long single segment has no earlier
        last-segment-start candidate to exercise the floor against -- the
        floor's own behavior is ``reload_policy``'s responsibility; this pins
        that the two stay wired together."""
        from civiccast.egress.gst.reload_policy import rollover_trigger_at

        plan_end_at = _NOW + timedelta(seconds=1800)  # a 30-minute single segment
        last_segment_start_at = _NOW  # the only segment starts at plan start
        trigger_at = rollover_trigger_at(
            plan_end_at=plan_end_at,
            last_segment_start_at=last_segment_start_at,
            min_lead_seconds=ChannelAutomationService._ROLLOVER_MIN_LEAD_SECONDS,
        )
        assert trigger_at == last_segment_start_at  # the earlier candidate wins
        assert ChannelAutomationService._ROLLOVER_MIN_LEAD_SECONDS == 120.0


class TestRolloverCadence:
    """Hostile-review fix (2026-09-05): D43 (#170) and this PR's first pass at
    D45 both encoded a premise that was never the shipped shape. D43's
    ``PLAN_MIN_SECONDS=1800`` (a 60-segment, 1800-second plan for 30-second
    schedule items) is reverted -- see ``source_plan.py``. What a
    30-second-item schedule actually builds now is an 8-segment, 240-second
    plan (``max_segments=8``), and these tests exercise the REAL
    ``ChannelAutomationService`` against that shape (and a longer one) using
    ``_HorizonAwareDaemon``/``_plan_with_segments`` -- the same dispatched-
    plan-horizon fixtures ``TestRolloverHorizonComesFromTheDispatchedPlan``
    uses, which is what the real ``EgressDaemon`` (not a re-query-only fake)
    gives the service to work with in production.
    """

    def _on_air_state(
        self, store: InMemoryEgressStore, channel_id: str, *, proof_event_id: str
    ) -> None:
        store.write_state(
            EgressStateRow(
                channel_id=channel_id,
                state="ON_AIR",
                current_source_label="Program",
                current_proof_event_id=proof_event_id,
                updated_at=_NOW,
            )
        )

    def _simulate(
        self,
        durations: tuple[float, ...],
        *,
        total_ticks: float,
        step: float = 2.0,
        min_interval_override: Callable[[float], float] | None = None,
    ) -> tuple[list[float], int]:
        """Tick a real ``ChannelAutomationService`` (backed by
        ``_HorizonAwareDaemon``, mirroring how the real ``EgressDaemon``
        reports what it actually dispatched) through ``total_ticks`` seconds
        of the real 2-second poll cadence, and return every tick a rollover
        reload was dispatched, plus how many times the source-plan provider
        was called. ``min_interval_override``, when given, replaces
        ``_rollover_min_interval_seconds`` entirely -- used to reproduce a
        prior (buggy) floor formula for comparison against the shipped one."""

        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _HorizonAwareDaemon(live_channels={"public"})
        # The plan actually on air from the channel's initial start.
        daemon.dispatched["public"] = ("ev-1", durations, False)
        calls = {"n": 0}

        def provider(cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_segments(cid, durations, source_ref_prefix=f"call{calls['n']}")

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )
        if min_interval_override is not None:
            service._rollover_min_interval_seconds = min_interval_override  # type: ignore[method-assign]

        dispatch_times: list[float] = []
        tick = 0.0
        ev_n = 1
        while tick < total_ticks:
            clock["now"] = 1000.0 + tick
            service.run_once(now=_NOW + timedelta(seconds=tick))
            if _pending_actions(store, "public") == ["reload"]:
                dispatch_times.append(tick)
                ev_n += 1
                new_ev = f"ev-{ev_n}"
                # The daemon executes the reload: it re-fetches the plan (the
                # same call a real _try_content_reload makes) and records it
                # as dispatched, DEFERRED (the channel is ON_AIR with no
                # manual override, so reload_policy.should_defer_switch is
                # True) -- this is what lets automation's next horizon be
                # dated from the OUTGOING leg's own end.
                daemon.dispatched["public"] = (new_ev, durations, True)
                self._on_air_state(store, "public", proof_event_id=new_ev)
            tick += step
        return dispatch_times, calls["n"]

    def test_thirty_second_items_roll_over_every_240_seconds_well_before_eos(
        self,
    ) -> None:
        """The regression this whole pass exists for, in its shipped shape:
        an 8-segment, 30-second-item plan is 240 seconds. The first rollover
        fires at the boundary-aligned trigger (120s -- ``_ROLLOVER_MIN_LEAD_
        SECONDS`` before the 240s end), and each later one fires 240s after
        the last -- always 120s (comfortably >= 100s) before that plan's own
        end, never at or after it. No FALLBACK_SLATE/EOS boundary is ever
        reached: every dispatch lands well inside the plan it is extending.
        """
        dispatch_times, calls = self._simulate((30.0,) * 8, total_ticks=1200)

        assert dispatch_times == [120.0, 360.0, 600.0, 840.0, 1080.0]
        # Each dispatch is 120s ahead of the plan boundary it is extending
        # (dispatch N's plan ends at 240*(N+1)) -- well over the 100s floor
        # this test exists to pin.
        for index, dispatch_at in enumerate(dispatch_times):
            plan_end_at = 240.0 * (index + 1)
            assert plan_end_at - dispatch_at >= 100.0
        # Not re-queried on every poll tick: once to establish (from the
        # dispatched plan, no provider call needed) is free; each trigger
        # re-queries once to confirm there's more schedule to roll onto.
        assert calls == len(dispatch_times)

    def test_sixty_seven_second_items_roll_over_at_416_of_536(self) -> None:
        """A different item length is not a special case: an 8-segment,
        67-second-item plan is 536 seconds, and the same boundary-aligned
        120s lead applies -- the first rollover fires at 416s (536 - 120),
        with 120s of runway before EOS."""
        dispatch_times, _calls = self._simulate((67.0,) * 8, total_ticks=1700)

        assert dispatch_times == [416.0, 952.0, 1488.0]
        for index, dispatch_at in enumerate(dispatch_times):
            plan_end_at = 536.0 * (index + 1)
            assert plan_end_at - dispatch_at >= 100.0

    def test_a_very_short_plan_still_rolls_over_before_its_own_end(self) -> None:
        """Item 3's regression case: an 8x3s (24-second) plan.

        Hostile-review fix (NEW-3, 2026-09-05): the shipped
        ``_rollover_min_interval_seconds`` floor alone was not enough --
        with a flat 120s ``_ROLLOVER_MIN_LEAD_SECONDS``,
        ``rollover_trigger_at`` still landed at or after a plan this short
        actually ends (``plan_end_at - 120`` is deep in the plan's own
        past), so dispatches happened but at/after EOS. Scaling the LEAD
        the same way (``_rollover_min_lead_seconds`` -- half the plan's
        duration, clamped at the historic 120s ceiling) fixes this too:
        every rollover now lands with real runway to spare, not just
        "eventually, somehow, still dispatching."

        MEASURED with both fixes shipped: a rollover every 24s (at 12s,
        36s, 60s, ...), each with a lead of exactly 12s -- half the plan's
        24-second life -- over the boundary it is extending."""
        dispatch_times, _calls = self._simulate((3.0,) * 8, total_ticks=300)

        assert len(dispatch_times) >= 5, dispatch_times

        def leads(dispatch_times: list[float]) -> list[float]:
            return [
                24.0 * (index + 1) - dispatch_at for index, dispatch_at in enumerate(dispatch_times)
            ]

        plan_leads = leads(dispatch_times)
        # The real invariant this test exists to pin: every dispatch lands
        # with meaningful runway before the plan it is extending actually
        # ends -- never "eventually dispatches, but after EOS."
        assert all(lead >= 0.25 * 24.0 for lead in plan_leads), plan_leads

    def test_the_flat_floor_bug_the_scaled_floor_fixes(self) -> None:
        """The CHANGELOG-cited measurement, reproduced directly: against an
        8x30s (240-second) plan, a flat 300s floor (D43's original value)
        dispatches with the boundary-aligned lead shrinking cycle over
        cycle until it goes negative -- the trigger arrives at or after the
        plan's real end and the engine would have reached EOS and
        restarted. The shipped, scaled floor (used by every other test in
        this class) keeps that lead at a steady ~120s indefinitely instead.

        The stale-horizon recovery changes the exact broken cadence once the
        flat floor pushes a dispatch to the boundary: it immediately rescues
        the expired plan instead of waiting for the flat floor again. The
        lead still goes negative because the floor remains wrong for this
        plan shape. The scaled floor below remains the real cadence fix."""

        def flat_300s_floor(_planned_seconds: float) -> float:
            return 300.0

        buggy_dispatches, _ = self._simulate(
            (30.0,) * 8, total_ticks=1800, min_interval_override=flat_300s_floor
        )
        fixed_dispatches, _ = self._simulate((30.0,) * 8, total_ticks=1800)

        def leads(dispatch_times: list[float]) -> list[float]:
            return [
                240.0 * (index + 1) - dispatch_at
                for index, dispatch_at in enumerate(dispatch_times)
            ]

        buggy_leads = leads(buggy_dispatches)
        fixed_leads = leads(fixed_dispatches)

        assert buggy_dispatches[:3] == [120.0, 420.0, 720.0]
        assert buggy_leads[:3] == [120.0, 60.0, 0.0]
        assert min(buggy_leads) < 0  # the flat floor is still not safe

        assert fixed_dispatches[:5] == [120.0, 360.0, 600.0, 840.0, 1080.0]
        assert all(lead >= 100.0 for lead in fixed_leads)

    def test_a_short_plan_cannot_dispatch_faster_than_its_scaled_minimum_interval(
        self,
    ) -> None:
        """D45: the CADENCE floor now scales with the plan actually on air
        (``_rollover_min_interval_seconds``) instead of a fixed 300s -- a flat
        300s floor is longer than the lifetime of a short plan (e.g. an
        8-segment, 30-second-item plan is only 240s), which would silently
        starve every rollover after the first until the engine hits EOS and
        restarts. For a 100-second plan the scaled floor is 50s (half the
        plan, clamped to the 30s-300s range): still a real floor under the
        cadence, but one a short plan can actually live inside."""
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        plans = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            plans["n"] += 1
            return _plan_with_duration("public", 100.0, source_ref=f"seg-{plans['n']}")

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )
        assert service._rollover_min_interval_seconds(100.0) == 50.0

        # Establish a 100s horizon, then trigger (a 100s plan is already
        # inside its own 120s lead floor).
        service.run_once(now=_NOW)
        service.run_once(now=_NOW + timedelta(seconds=1))
        assert _pending_actions(store, "public") == ["reload"]

        # The reload lands -> a fresh proof event -> a fresh 100s horizon that
        # is immediately inside its own lead floor again. Without the scaled
        # interval floor this would dispatch again straight away, once per
        # plan.
        self._on_air_state(store, "public", proof_event_id="ev-2")
        clock["now"] += 40.0  # 40s since the dispatch, < the 50s scaled floor
        service.run_once(now=_NOW + timedelta(seconds=41))
        service.run_once(now=_NOW + timedelta(seconds=42))
        assert _pending_actions(store, "public") == []

        # Past the scaled floor, the next one is allowed through -- well
        # before this 100-second plan would otherwise reach EOS.
        clock["now"] += 20.0  # 60s since the dispatch, > the 50s scaled floor
        service.run_once(now=_NOW + timedelta(seconds=61))
        service.run_once(now=_NOW + timedelta(seconds=62))
        assert _pending_actions(store, "public") == ["reload"]

    def test_the_scaled_interval_never_exceeds_the_historic_ceiling(self) -> None:
        """A long plan (>= 600s) is unaffected by D45: the scaled floor
        clamps back down to the historic flat 300s, same as before. A short
        plan's floor is always HALF its own duration, never a flat 30s
        (paired with the real-dispatch behavioral tests above, which show
        what this number actually does to the automation's cadence)."""
        assert ChannelAutomationService._ROLLOVER_MIN_INTERVAL_SECONDS == 300.0
        service = ChannelAutomationService(
            InMemoryEgressStore(),
            _FakeDaemon(),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
        )
        assert service._rollover_min_interval_seconds(1800.0) == 300.0
        assert service._rollover_min_interval_seconds(10.0) == 5.0

    def test_the_scaled_interval_never_exceeds_half_a_24_second_plan(self) -> None:
        """Item 3's exact regression case: an intermediate version of this
        fix floored the interval at a flat 30s (``max(30.0, 0.5 *
        planned_seconds)``), which for a very short plan is LONGER than the
        plan's entire life.

        MEASURED (with the shipped, ALSO-scaled lead --
        ``_rollover_min_lead_seconds``, NEW-3 -- in place; a real dispatch
        simulation, not a one-off unit check): an 8x3s (24-second) plan with
        that flat 30s floor still dispatches ten times in this window (at
        12s, 42s, 72s, ... 282s), NOT "exactly one rollover, ever" -- but the
        boundary-aligned lead shrinks every cycle (12s, 6s, 0s, then
        negative from the fourth rollover on), so once it turns negative
        the trigger is arriving at or after the plan's real end: the same
        failure mode this whole mechanism exists to prevent, just at a
        smaller scale and slower to show up than "one dispatch and then
        nothing." The fixed floor (``_ROLLOVER_MIN_INTERVAL_FLOOR_SECONDS``
        = 1.0, a trivial epsilon) must never return more than half of
        ``planned_seconds`` -- see
        ``test_a_very_short_plan_still_rolls_over_before_its_own_end`` above
        for the shipped floor's actual (positive-lead, indefinite) cadence."""
        service = ChannelAutomationService(
            InMemoryEgressStore(),
            _FakeDaemon(),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
        )
        assert service._rollover_min_interval_seconds(24.0) == 12.0
        assert service._rollover_min_interval_seconds(24.0) <= 0.5 * 24.0

    def test_the_interval_floor_is_cleared_when_the_channel_leaves_the_air(self) -> None:
        """The floor governs EXTENSIONS of a live plan. A channel that goes
        dark and comes back must not be throttled by an earlier rollover --
        the slate replan and auto-start paths are unaffected."""
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air_state(store, "public", proof_event_id="ev-1")
        daemon = _FakeDaemon(live_channels={"public"})
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 150.0),
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)
        service.run_once(now=_NOW + timedelta(seconds=1))
        assert _pending_actions(store, "public") == ["reload"]

        # Off air and back on again, well inside the 300s interval.
        store.write_state(EgressStateRow(channel_id="public", state="STOPPED", updated_at=_NOW))
        clock["now"] += 10.0
        service.run_once(now=_NOW + timedelta(seconds=11))
        self._on_air_state(store, "public", proof_event_id="ev-2")
        clock["now"] += 10.0
        service.run_once(now=_NOW + timedelta(seconds=21))
        service.run_once(now=_NOW + timedelta(seconds=22))
        assert _pending_actions(store, "public") == ["reload"]


class TestPerChannelLatchIndependence:
    """CA-4: N channels in one pass with fully independent state/latches.

    Renamed from TestThreeChannelConcurrency (audit TEST-010): no
    concurrency is exercised here - the pass is sequential by design and
    these tests pin per-channel latch independence within one pass."""

    def _three_channel_store(self) -> InMemoryEgressStore:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        store.upsert_config(_config("education", auto_start=True))
        store.upsert_config(_config("government", auto_start=False))
        return store

    def test_one_pass_drives_all_enabled_channels_independently(self) -> None:
        store = self._three_channel_store()
        daemon = _FakeDaemon(live_channels={"education"})
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )

        seen = service.run_once(now=_NOW)

        assert seen == ["education", "government", "public"]
        assert daemon.processed == ["education", "government", "public"]
        # Only the dark auto_start channel gets a start; the live one and
        # the manual one are untouched.
        assert _pending_actions(store, "public") == ["start"]
        assert _pending_actions(store, "education") == []
        assert _pending_actions(store, "government") == []

    def test_one_channel_crash_restarts_only_that_channel(self) -> None:
        store = self._three_channel_store()
        daemon = _FakeDaemon(live_channels={"public", "education"})
        service = ChannelAutomationService(
            store, daemon, lambda _cid: None, settings=ChannelAutomationSettings()
        )
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []
        assert _pending_actions(store, "education") == []

        # public's encoder dies; education stays live.
        daemon.live_channels.discard("public")
        service.run_once(now=_NOW)

        assert _pending_actions(store, "public") == ["start"]
        assert _pending_actions(store, "education") == []
        assert _pending_actions(store, "government") == []

    def test_slate_replan_latches_are_per_channel(self) -> None:
        store = self._three_channel_store()
        for channel_id in ("public", "education"):
            store.write_state(
                EgressStateRow(channel_id=channel_id, state="FALLBACK_SLATE", updated_at=_NOW)
            )
        daemon = _FakeDaemon(live_channels={"public", "education", "government"})
        # Only education has a playable plan.
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan(cid) if cid == "education" else None,
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)

        assert _pending_actions(store, "education") == ["reload"]
        assert _pending_actions(store, "public") == []
        assert _pending_actions(store, "government") == []


class TestAutomationRollup:
    """CA-4: the System Health rollup of auto_start channel states."""

    def test_states_map_to_on_air_slate_and_dark(self) -> None:
        from civiccast.egress.automation import summarize_automation

        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        store.upsert_config(_config("education", auto_start=True))
        store.upsert_config(_config("government", auto_start=True))
        store.upsert_config(_config("manual", auto_start=False))
        store.upsert_config(_config("disabled", enabled=False, auto_start=True))
        store.write_state(EgressStateRow(channel_id="public", state="ON_AIR", updated_at=_NOW))
        store.write_state(
            EgressStateRow(channel_id="education", state="FALLBACK_SLATE", updated_at=_NOW)
        )
        # government has no state row at all -> dark.

        rollup = summarize_automation(store)

        assert rollup.automated == 3
        assert rollup.on_air == 1
        assert rollup.on_slate == 1
        assert rollup.dark == ["government"]

    def test_error_and_stopped_states_are_dark(self) -> None:
        from civiccast.egress.automation import summarize_automation

        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        store.write_state(EgressStateRow(channel_id="public", state="ERROR", updated_at=_NOW))

        assert summarize_automation(store).dark == ["public"]


class TestSettings:
    def test_from_env_validates_mode_and_poll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CIVICCAST_CHANNEL_AUTOMATION", "sometimes")
        with pytest.raises(ValueError, match="CIVICCAST_CHANNEL_AUTOMATION"):
            ChannelAutomationSettings.from_env()

        monkeypatch.setenv("CIVICCAST_CHANNEL_AUTOMATION", "off")
        monkeypatch.setenv("CIVICCAST_CHANNEL_AUTOMATION_POLL_SECONDS", "0")
        with pytest.raises(ValueError, match="positive"):
            ChannelAutomationSettings.from_env()

        monkeypatch.setenv("CIVICCAST_CHANNEL_AUTOMATION_POLL_SECONDS", "5")
        settings = ChannelAutomationSettings.from_env()
        assert settings.mode == "off"
        assert settings.poll_seconds == 5.0

        monkeypatch.delenv("CIVICCAST_CHANNEL_AUTOMATION")
        monkeypatch.delenv("CIVICCAST_CHANNEL_AUTOMATION_POLL_SECONDS")
        assert ChannelAutomationSettings.from_env().mode == "inline"


class TestNdiRelay:
    """Issue #116: the automation pass supervises per-channel NDI relays."""

    @staticmethod
    def _ndi_config(channel_id: str = "public") -> EgressConfig:
        return _config(channel_id).model_copy(
            update={
                "ndi_relay_name": "CivicCast Public",
                "sinks": [
                    EgressSinkSpec(
                        kind="udp-ts",
                        label="Cable headend",
                        uri="udp://127.0.0.1:23101",
                        extra_output_args=["-muxrate", "8000k"],
                    )
                ],
            }
        )

    class _FakeRelay:
        def __init__(self, channel_id: str, ndi_name: str, source_uri: str) -> None:
            from civiccast.egress.ndi_relay import NdiRelayStatus

            self.channel_id = channel_id
            self.ndi_name = ndi_name
            self.source_uri = source_uri
            self.ensured = 0
            self.stopped = False
            self._status = NdiRelayStatus(
                channel_id=channel_id, ndi_name=ndi_name, state="running", pid=99
            )

        def ensure_running(self):  # type: ignore[no-untyped-def]
            self.ensured += 1
            return self._status

        def stop(self) -> None:
            self.stopped = True

        def status(self):  # type: ignore[no-untyped-def]
            return self._status

    def test_named_channel_gets_one_supervised_relay(self) -> None:
        from civiccast.egress.ndi_relay import clear_relay_statuses, get_relay_status

        clear_relay_statuses()
        store = InMemoryEgressStore()
        store.upsert_config(self._ndi_config())
        created: list[TestNdiRelay._FakeRelay] = []

        def factory(*, channel_id, ndi_name, source_uri):  # type: ignore[no-untyped-def]
            relay = self._FakeRelay(channel_id, ndi_name, source_uri)
            created.append(relay)
            return relay

        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            ndi_supervisor_factory=factory,
        )

        service.run_once(now=_NOW)
        service.run_once(now=_NOW)

        assert len(created) == 1
        assert created[0].source_uri == "udp://127.0.0.1:23101"
        assert created[0].ensured == 2
        status = get_relay_status("public")
        assert status is not None and status.state == "running"

    def test_clearing_the_name_stops_the_relay(self) -> None:
        from civiccast.egress.ndi_relay import clear_relay_statuses, get_relay_status

        clear_relay_statuses()
        store = InMemoryEgressStore()
        store.upsert_config(self._ndi_config())
        created: list[TestNdiRelay._FakeRelay] = []

        def factory(*, channel_id, ndi_name, source_uri):  # type: ignore[no-untyped-def]
            relay = self._FakeRelay(channel_id, ndi_name, source_uri)
            created.append(relay)
            return relay

        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            ndi_supervisor_factory=factory,
        )
        service.run_once(now=_NOW)
        assert created[0].ensured == 1

        store.upsert_config(self._ndi_config().model_copy(update={"ndi_relay_name": None}))
        service.run_once(now=_NOW)

        assert created[0].stopped is True
        assert get_relay_status("public") is None

    def test_channel_without_udp_sink_reports_blocked(self) -> None:
        from civiccast.egress.ndi_relay import clear_relay_statuses, get_relay_status

        clear_relay_statuses()
        store = InMemoryEgressStore()
        store.upsert_config(_config("public").model_copy(update={"ndi_relay_name": "X"}))

        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            ndi_supervisor_factory=lambda **kw: pytest.fail("must not spawn"),
        )
        service.run_once(now=_NOW)

        status = get_relay_status("public")
        assert status is not None
        assert status.state == "blocked"
        assert "udp" in status.next_step.lower()


class TestSdiRelay:
    """Issue #117: the automation pass supervises per-channel SDI relays."""

    @staticmethod
    def _sdi_config(channel_id: str = "public") -> EgressConfig:
        return _config(channel_id).model_copy(
            update={
                "sdi_relay_device": "DeckLink Mini Monitor 4K",
                "sinks": [
                    EgressSinkSpec(
                        kind="udp-ts",
                        label="Cable headend",
                        uri="udp://127.0.0.1:23101",
                        extra_output_args=["-muxrate", "8000k"],
                    )
                ],
            }
        )

    class _FakeRelay:
        def __init__(self, channel_id: str, device: str, source_uri: str) -> None:
            from civiccast.egress.sdi_relay import SdiRelayStatus

            self.channel_id = channel_id
            self.device = device
            self.source_uri = source_uri
            self.ensured = 0
            self.stopped = False
            self._status = SdiRelayStatus(
                channel_id=channel_id, device=device, state="running", pid=99
            )

        def ensure_running(self):  # type: ignore[no-untyped-def]
            self.ensured += 1
            return self._status

        def stop(self) -> None:
            self.stopped = True

        def status(self):  # type: ignore[no-untyped-def]
            return self._status

    def test_configured_channel_gets_one_supervised_relay(self) -> None:
        from civiccast.egress.sdi_relay import clear_relay_statuses, get_relay_status

        clear_relay_statuses()
        store = InMemoryEgressStore()
        store.upsert_config(self._sdi_config())
        created: list[TestSdiRelay._FakeRelay] = []

        def factory(*, channel_id, device, source_uri):  # type: ignore[no-untyped-def]
            relay = self._FakeRelay(channel_id, device, source_uri)
            created.append(relay)
            return relay

        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            sdi_supervisor_factory=factory,
        )

        service.run_once(now=_NOW)
        service.run_once(now=_NOW)

        assert len(created) == 1
        assert created[0].source_uri == "udp://127.0.0.1:23101"
        assert created[0].device == "DeckLink Mini Monitor 4K"
        assert created[0].ensured == 2
        status = get_relay_status("public")
        assert status is not None and status.state == "running"

    def test_clearing_the_device_stops_the_relay(self) -> None:
        from civiccast.egress.sdi_relay import clear_relay_statuses, get_relay_status

        clear_relay_statuses()
        store = InMemoryEgressStore()
        store.upsert_config(self._sdi_config())
        created: list[TestSdiRelay._FakeRelay] = []

        def factory(*, channel_id, device, source_uri):  # type: ignore[no-untyped-def]
            relay = self._FakeRelay(channel_id, device, source_uri)
            created.append(relay)
            return relay

        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            sdi_supervisor_factory=factory,
        )
        service.run_once(now=_NOW)
        assert created[0].ensured == 1

        store.upsert_config(self._sdi_config().model_copy(update={"sdi_relay_device": None}))
        service.run_once(now=_NOW)

        assert created[0].stopped is True
        assert get_relay_status("public") is None

    def test_channel_without_udp_sink_reports_blocked(self) -> None:
        from civiccast.egress.sdi_relay import clear_relay_statuses, get_relay_status

        clear_relay_statuses()
        store = InMemoryEgressStore()
        store.upsert_config(_config("public").model_copy(update={"sdi_relay_device": "DeckLink"}))

        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            sdi_supervisor_factory=lambda **kw: pytest.fail("must not spawn"),
        )
        service.run_once(now=_NOW)

        status = get_relay_status("public")
        assert status is not None
        assert status.state == "blocked"
        assert "udp" in status.next_step.lower()

    def test_device_change_replaces_the_relay(self) -> None:
        from civiccast.egress.sdi_relay import clear_relay_statuses

        clear_relay_statuses()
        store = InMemoryEgressStore()
        store.upsert_config(self._sdi_config())
        created: list[TestSdiRelay._FakeRelay] = []

        def factory(*, channel_id, device, source_uri):  # type: ignore[no-untyped-def]
            relay = self._FakeRelay(channel_id, device, source_uri)
            created.append(relay)
            return relay

        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            sdi_supervisor_factory=factory,
        )
        service.run_once(now=_NOW)
        store.upsert_config(
            self._sdi_config().model_copy(update={"sdi_relay_device": "DeckLink Duo 2"})
        )
        service.run_once(now=_NOW)

        assert len(created) == 2
        assert created[0].stopped is True
        assert created[1].device == "DeckLink Duo 2"


class TestPredecessorRelayReap:
    """Audit ENG-003: SDI/NDI relay processes were outside the orphan reap -
    after an unclean restart a dead server's relay holds the DeckLink card
    while the new relay backoff-loops forever. At automation boot, any
    pre-boot ffmpeg whose command line is a relay (-f decklink /
    -f libndi_newtek) is reaped."""

    def test_pre_boot_relay_processes_are_reaped(self) -> None:
        from civiccast.egress.automation import reap_predecessor_relays

        killed: list[tuple[int, float]] = []
        survivors = [
            # (pid, name, cmdline, created_at) - boot epoch is 1000.0
            (101, "ffmpeg.exe", "ffmpeg -i udp://127.0.0.1:23101 -f decklink DeckLink", 10.0),
            (102, "ffmpeg.exe", "ffmpeg -i udp://... -f libndi_newtek CivicCast", 20.0),
            (103, "ffmpeg.exe", "ffmpeg -i in.mp4 -f mpegts out.ts", 10.0),  # encoder, not relay
            (104, "ffmpeg.exe", "ffmpeg -i udp://... -f decklink X", 2000.0),  # post-boot
            (105, "notepad.exe", "notepad -f decklink", 10.0),  # not ffmpeg
        ]

        reap_predecessor_relays(
            boot_epoch=1000.0,
            scanner=lambda: list(survivors),
            terminator=lambda pid, created_at: killed.append((pid, created_at)),
        )

        assert killed == [(101, 10.0), (102, 20.0)]

    def test_reaped_relays_are_recorded_as_proof_events(self) -> None:
        # S9-4: when a store is given, each reap lands a durable coprocess-lifecycle
        # proof event — and a NON-relay process produces neither a reap nor an event.
        from civiccast.egress.automation import reap_predecessor_relays

        store = InMemoryEgressStore()
        survivors = [
            (102, "ffmpeg.exe", "ffmpeg -i udp://... -f libndi_newtek CivicCast", 20.0),
            (103, "ffmpeg.exe", "ffmpeg -i in.mp4 -f mpegts out.ts", 10.0),  # encoder, not a relay
        ]
        reaped = reap_predecessor_relays(
            boot_epoch=1000.0,
            scanner=lambda: list(survivors),
            terminator=lambda _pid, _ct: None,
            store=store,
        )
        assert reaped == [102]  # only the NDI relay, not the encoder
        events = store.recent_proof_events("egress-system", 10)
        assert len(events) == 1
        assert events[0].proof_boundary == "civiccast-egress-coprocess-lifecycle"
        assert "102" in events[0].source_path
        assert all("103" not in event.source_path for event in events)  # encoder never recorded

    def test_reap_without_store_still_reaps_but_emits_no_proof_events(self) -> None:
        # backward-compat: store omitted → prior behavior (reaps, no audit sink)
        from civiccast.egress.automation import reap_predecessor_relays

        reaped = reap_predecessor_relays(
            boot_epoch=1000.0,
            scanner=lambda: [(102, "ffmpeg.exe", "ffmpeg -f libndi_newtek X", 20.0)],
            terminator=lambda _pid, _ct: None,
        )
        assert reaped == [102]


class TestReloadChurnCooldown:
    """Audit ENG-002: a due program whose PREPARATION persistently fails
    (deleted/corrupt media) used to drive a reload->kill->slate cycle every
    ~2 ticks for the item's whole duration - a TS-reset storm worse than the
    one #154 fixed. Reloads get the same cooldown medicine as #152 starts."""

    def _service(self, store: InMemoryEgressStore, clock: dict) -> ChannelAutomationService:  # type: ignore[type-arg]
        return ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda _cid: _plan("public"),
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

    def _slate_state(self, store: InMemoryEgressStore, state: str) -> None:
        store.write_state(
            EgressStateRow(
                channel_id="public",
                state=state,  # type: ignore[arg-type]
                current_source_label="CivicCast slate",
                updated_at=_NOW,
            )
        )

    def test_prep_failure_flap_does_not_strobe_reloads(self) -> None:
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        service = self._service(store, clock)

        # Slate gap with a due plan: one reload.
        self._slate_state(store, "FALLBACK_SLATE")
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["reload"]

        # The daemon kills filler, the start fails preparation, the channel
        # flaps TRANSITIONING -> FALLBACK_SLATE (which used to clear the
        # one-shot latch). Within the cooldown: NO new reload.
        self._slate_state(store, "TRANSITIONING")
        clock["now"] += 2.0
        service.run_once(now=_NOW)
        self._slate_state(store, "FALLBACK_SLATE")
        clock["now"] += 2.0
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

        # Past the cooldown and still on slate with a due plan: retry.
        clock["now"] += 60.0
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["reload"]


class TestStartLatchRecovery:
    """Issue #152: a dark auto_start channel must be retried with bounded
    pacing, not exactly once - the one-shot latch deadlocked a channel for
    an hour during the CA-8 acceptance run."""

    def test_dark_channel_start_reissues_after_cooldown(self) -> None:
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        daemon = _FakeDaemon()  # never goes live: starts keep failing
        service = ChannelAutomationService(
            store,
            daemon,
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["start"]

        # Within the cooldown: no command storm.
        clock["now"] += 5.0
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

        # Past the cooldown and still dark: the driver tries again.
        clock["now"] += 60.0
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["start"]

    def test_live_channel_clears_the_retry_pacing(self) -> None:
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public", auto_start=True))
        daemon = _FakeDaemon(live_channels={"public"})
        service = ChannelAutomationService(
            store,
            daemon,
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == []

        # Channel drops: a start goes out immediately (no stale pacing).
        daemon.live_channels.clear()
        service.run_once(now=_NOW)
        assert _pending_actions(store, "public") == ["start"]


class TestRolloverHorizonComesFromTheDispatchedPlan:
    """Hostile-review (d): the rollover horizon must be derived from the plan the
    daemon ACTUALLY dispatched, not from a fresh call to the source plan provider.

    The provider re-windows from whatever schedule item is live at the moment of the
    call and caps the result at ``max_segments``, so calling it again returns a
    DIFFERENT segment list than the one on air. Summing that list and calling it "when
    the airing plan ends" can overshoot the real end -- and since
    ``rollover_trigger_at`` is computed from that number, the trigger then lands at or
    after the boundary it exists to get ahead of, which is the original EOS-restart
    defect wearing a different hat.

    Every case below uses MULTI-SEGMENT plans on purpose: with a single segment the
    plan's last-segment-start IS its start, so ``rollover_trigger_at`` returns the
    plan's own start time and every horizon -- right or wrong -- triggers on the next
    tick. A single-segment scenario cannot tell the two apart.
    """

    def _on_air(self, store: InMemoryEgressStore, channel_id: str, proof_event_id: str) -> None:
        store.write_state(
            EgressStateRow(
                channel_id=channel_id,
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id=proof_event_id,
                updated_at=_NOW,
            )
        )

    #: The plan actually on air: 600s of first segment, then a 40s tail. Its real end
    #: is _NOW+640 and its boundary-aligned trigger is _NOW+520 (120s lead floor).
    _AIRING = (600.0, 40.0)
    #: What a re-query answers instead: the provider has re-windowed forward and is
    #: describing 1800s of schedule that is NOT the plan on air. Believing it puts the
    #: horizon at _NOW+1800 and the trigger at _NOW+900 -- 260s PAST the real end.
    _REQUERY = (900.0, 900.0)

    def test_an_over_reporting_re_query_no_longer_hides_an_imminent_boundary(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-1")
        daemon = _HorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", self._AIRING, False)
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_segments(cid, self._REQUERY),
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)  # establishes the horizon from the DISPATCHED plan
        assert _pending_actions(store, "public") == []
        # _NOW+560 is past the airing plan's own trigger (_NOW+520) and still 80s
        # short of its end -- the whole point of a boundary-aligned rollover.
        service.run_once(now=_NOW + timedelta(seconds=560))
        assert _pending_actions(store, "public") == ["reload"]

    def test_the_legacy_re_query_would_have_slept_through_that_boundary(self) -> None:
        """The control for the test above: the SAME store, provider and clock, with a
        daemon that cannot report its dispatch. Nothing rolls over before the real end
        -- which is exactly the behaviour the fix replaces."""
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-1")
        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda cid: _plan_with_segments(cid, self._REQUERY),
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)
        service.run_once(now=_NOW + timedelta(seconds=560))
        assert _pending_actions(store, "public") == []

    def test_a_dispatch_record_for_a_different_proof_event_is_ignored(self) -> None:
        """A record the current proof event does not match is not evidence about what
        is on air now -- fall back to the legacy re-query rather than dating the
        horizon off a stale dispatch."""
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-2")
        daemon = _HorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", self._AIRING, False)  # stale
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_segments(cid, self._REQUERY),
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)
        service.run_once(now=_NOW + timedelta(seconds=560))
        assert _pending_actions(store, "public") == []

    def test_a_deferred_rollover_plan_is_dated_from_the_OUTGOING_plan_s_end(self) -> None:
        """A boundary-aligned rollover plan does not start when it is dispatched: the
        engine holds it, prerolled, until the outgoing leg's own end
        (``switch_at_end_of_current``). Dating its horizon from the dispatch instant
        instead would put the projected end a whole rollover lead time early, and make
        the NEXT rollover fire before there was anything to roll onto."""
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-1")
        daemon = _HorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", (500.0, 100.0), False)  # ends _NOW+600
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_segments(cid, (900.0, 900.0)),
            settings=ChannelAutomationSettings(),
        )
        service.run_once(now=_NOW)
        store.pop_pending_commands("public")

        # 500s in, a DEFERRED rollover lands: 300s of new content that will not begin
        # until the outgoing plan ends at _NOW+600, so it ends at _NOW+900 -- not at
        # (_NOW+500)+300 = _NOW+800.
        self._on_air(store, "public", "ev-2")
        daemon.dispatched["public"] = ("ev-2", (200.0, 100.0), True)
        service.run_once(now=_NOW + timedelta(seconds=500))
        assert _pending_actions(store, "public") == []

        # _NOW+700 is past the trigger a dispatch-instant horizon would have computed
        # (min(_NOW+700, _NOW+680) = _NOW+680) and short of the correct one
        # (min(_NOW+800, _NOW+780) = _NOW+780). Nothing may be dispatched here.
        service.run_once(now=_NOW + timedelta(seconds=700))
        assert _pending_actions(store, "public") == []

        # Past the correct trigger it rolls over as normal.
        service.run_once(now=_NOW + timedelta(seconds=800))
        assert _pending_actions(store, "public") == ["reload"]


class TestPerChannelClockIsReadFreshEachIteration:
    """Item 78 fix 1: ``run_once()`` (no explicit ``now`` -- the shape
    ``run_forever`` actually uses in production) must read wall-clock time
    freshly for EACH channel's pass, not once for the whole scan. Reading it
    once meant a single channel's synchronous ``_start`` (``SourcePreparer``
    can block for minutes, per soak evidence) left every channel that poll
    tick -- including its own next tick -- computing rollover math against a
    timestamp that was already stale by however long the block lasted,
    freezing ``plan_end_at`` in the past and firing rollovers forever ("live
    plan ends in -698s")."""

    def test_now_is_read_once_per_channel_not_once_for_the_whole_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("a"))
        store.upsert_config(_config("b"))
        store.upsert_config(_config("c"))
        service = ChannelAutomationService(
            store,
            _FakeDaemon(),
            lambda _cid: None,
            settings=ChannelAutomationSettings(),
        )

        import civiccast.egress.automation as automation_module

        real_datetime = automation_module.datetime
        call_count = 0

        class _CountingDateTime(real_datetime):  # type: ignore[misc,valid-type]
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                nonlocal call_count
                call_count += 1
                return real_datetime.now(tz)

        monkeypatch.setattr(automation_module, "datetime", _CountingDateTime)

        service.run_once()  # no explicit `now` -- the real run_forever shape

        assert call_count == 3  # once per enabled channel, never once for the whole pass

    def test_the_blocking_channel_itself_sees_the_post_block_clock_not_a_stale_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Coordinator review, round 2: reading "now" once per channel is not
        enough if it is read at the TOP of that channel's own pass -- BEFORE
        ``daemon.process_once``, which is exactly where a channel's own
        synchronous ``_start`` (``SourcePreparer.prepare``) can block for
        minutes. The blocking channel must see the POST-block clock for its
        own ``_check_plan_rollover``, not the timestamp captured before the
        block. Channel "a" here blocks (advances a shared fake wall clock by
        2000s, longer than its own 1800s plan) inside ``process_once``;
        channel "b" never blocks. Both establish a horizon strictly in the
        future relative to the POST-block clock -- not the "-200s" a
        pre-block reading would have produced for "a"."""
        store = InMemoryEgressStore()
        store.upsert_config(_config("a"))
        store.upsert_config(_config("b"))

        def on_air(channel_id: str, proof_event_id: str) -> None:
            store.write_state(
                EgressStateRow(
                    channel_id=channel_id,
                    state="ON_AIR",
                    current_source_label="Program",
                    current_proof_event_id=proof_event_id,
                    updated_at=_NOW,
                )
            )

        on_air("a", "ev-a1")
        on_air("b", "ev-b1")

        import civiccast.egress.automation as automation_module

        wall_clock = {"now": _NOW}
        real_datetime = automation_module.datetime

        class _ControllableDateTime(real_datetime):  # type: ignore[misc,valid-type]
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return wall_clock["now"]

        monkeypatch.setattr(automation_module, "datetime", _ControllableDateTime)

        class _BlockingDaemon(_FakeDaemon):
            def process_once(self, channel_id: str) -> int:
                super().process_once(channel_id)
                if channel_id == "a":
                    # Simulate item 78's diagnosed ~915s SourcePreparer block
                    # (here 2000s -- longer than the 1800s plan below, so a
                    # stale pre-block "now" would establish an already-past
                    # horizon, not merely a short-lead one).
                    wall_clock["now"] = wall_clock["now"] + timedelta(seconds=2000)
                return 0

        daemon = _BlockingDaemon(live_channels={"a", "b"})
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 1800.0),
            settings=ChannelAutomationSettings(),
        )

        service.run_once()  # no explicit `now` -- the real run_forever shape

        post_block_now = wall_clock["now"]
        tracked_a = service._plan_horizon["a"]
        tracked_b = service._plan_horizon["b"]
        # A stale pre-block reading would have established plan_end_at at
        # _NOW+1800, which is 200s BEHIND post_block_now (_NOW+2000) -- the
        # exact "-200s" shape of the diagnosed bug. The fix must establish it
        # from the post-block clock instead, strictly in the future.
        assert tracked_a[1] > post_block_now
        assert tracked_a[1] == post_block_now + timedelta(seconds=1800)
        assert tracked_b[1] > post_block_now


class TestStaleRolloverHorizonDispatchesImmediateRecovery:
    """A live finite plan that is already past due must switch to the current
    schedule item instead of repeatedly re-dating the expired dispatch."""

    def test_a_stale_horizon_dispatches_once_against_its_original_boundary(
        self,
    ) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        store.write_state(
            EgressStateRow(
                channel_id="public",
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id="ev-1",
                updated_at=_NOW,
                pid=123,
            )
        )

        class _RecordingDaemon(_HorizonAwareDaemon):
            def __init__(self) -> None:
                super().__init__(live_channels={"public"})
                self.dispatched["public"] = ("ev-1", (100.0,), False)
                self.recorded: list[tuple[datetime, str | None]] = []

            def record_rollover_plan_end(
                self,
                _channel_id: str,
                plan_end_at: datetime,
                *,
                command_id: str | None,
                force_fallback: bool = False,
                min_plan_seconds: float | None = None,
            ) -> None:
                self.recorded.append((plan_end_at, command_id))

        daemon = _RecordingDaemon()
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 100.0),
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=lambda cid, _at: _plan_with_duration(
                cid, 100.0, source_ref="current-programme"
            ),
        )

        service.run_once(now=_NOW)  # establishes: ends at _NOW+100
        assert _pending_actions(store, "public") == []
        # A recent ordinary dispatch must not suppress stale-plan recovery.
        service._rollover_dispatched_at["public"] = service._monotonic()

        far_future = _NOW + timedelta(seconds=5000)
        service.run_once(now=far_future)

        pending = store.peek_pending_commands("public")
        assert [command.action for command in pending] == ["reload"]
        assert daemon.recorded == [(_NOW + timedelta(seconds=100), pending[0].command_id)]
        assert service._plan_horizon["public"][1] == _NOW + timedelta(seconds=100)

        # The issued latch prevents a second command while the first is waiting
        # for the daemon to consume it.
        service.run_once(now=far_future + timedelta(seconds=2))
        assert len(store.peek_pending_commands("public")) == 1

    def test_stale_short_horizon_waits_for_control_connection_then_dispatches_once(
        self,
    ) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("education"))
        store.write_state(
            EgressStateRow(
                channel_id="education",
                state="ON_AIR",
                current_source_label="Education",
                current_proof_event_id="ev-1",
                updated_at=_NOW,
                pid=9876,
            )
        )

        class _ConnectingDaemon(_HorizonAwareDaemon):
            def __init__(self) -> None:
                super().__init__(live_channels={"education"})
                self.dispatched["education"] = ("ev-1", (30.0,), False)
                self.control_ready = False
                self.recorded: list[tuple[datetime, str | None]] = []

            def worker_initial_control_connection_observed(self, _channel_id: str) -> bool:
                return self.control_ready

            def record_rollover_plan_end(
                self,
                _channel_id: str,
                plan_end_at: datetime,
                *,
                command_id: str | None,
                force_fallback: bool = False,
                min_plan_seconds: float | None = None,
            ) -> None:
                self.recorded.append((plan_end_at, command_id))

        daemon = _ConnectingDaemon()
        provider_boundaries: list[datetime] = []

        def next_plan(channel_id: str, boundary: datetime) -> EgressSourcePlan:
            provider_boundaries.append(boundary)
            return _plan_with_duration(channel_id, 30.0, source_ref="next-education")

        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 30.0),
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=next_plan,
        )

        service.run_once(now=_NOW)  # establish the 30-second live horizon
        for seconds in (31, 33):
            service.run_once(now=_NOW + timedelta(seconds=seconds))

        assert provider_boundaries == []
        assert store.peek_pending_commands("education") == []
        assert "education" not in service._rollover_issued
        assert "education" not in service._rollover_dispatched_at
        assert daemon.recorded == []

        daemon.control_ready = True
        ready_at = _NOW + timedelta(seconds=35)
        service.run_once(now=ready_at)

        pending = store.peek_pending_commands("education")
        assert [command.action for command in pending] == ["reload"]
        assert provider_boundaries == [ready_at]
        assert daemon.recorded == [(_NOW + timedelta(seconds=30), pending[0].command_id)]
        assert "education" in service._rollover_issued

        service.run_once(now=ready_at + timedelta(seconds=2))
        assert len(store.peek_pending_commands("education")) == 1
        assert provider_boundaries == [ready_at]
        assert len(daemon.recorded) == 1

    def test_a_failing_stale_recovery_provider_keeps_its_retry_cooldown(self) -> None:
        clock = {"now": 0.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        store.write_state(
            EgressStateRow(
                channel_id="public",
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id="ev-1",
                updated_at=_NOW,
                pid=123,
            )
        )
        daemon = _HorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", (100.0,), False)
        provider_calls: list[datetime] = []

        def failing_provider(_channel_id: str, boundary: datetime) -> EgressSourcePlan:
            provider_calls.append(boundary)
            raise SourcePrepareError("not ready")

        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_duration(cid, 100.0),
            settings=ChannelAutomationSettings(),
            boundary_source_plan_provider=failing_provider,
            monotonic=lambda: clock["now"],
        )
        service.run_once(now=_NOW)
        far_future = _NOW + timedelta(seconds=5000)
        service.run_once(now=far_future)
        assert provider_calls == [far_future]

        clock["now"] = 2.0
        service.run_once(now=far_future + timedelta(seconds=2))
        assert provider_calls == [far_future]

        clock["now"] = 31.0
        service.run_once(now=far_future + timedelta(seconds=31))
        assert provider_calls == [far_future, far_future + timedelta(seconds=31)]


class _SettlingHorizonAwareDaemon(_HorizonAwareDaemon):
    """A daemon double with BOTH real-daemon capabilities the rollover pass
    consults: ``dispatched_plan_horizon`` and ``has_pending_reload_settlement``."""

    def __init__(self, *, live_channels: set[str] | None = None) -> None:
        super().__init__(live_channels=live_channels)
        self.pending_channels: set[str] = set()

    def has_pending_reload_settlement(self, channel_id: str) -> bool:
        return channel_id in self.pending_channels


class TestStaleHorizonWaitsForASettlingSeamlessReload:
    """Sandbox soak 39d852e (2026-09-09): the stale-horizon branch fired on
    every government-channel rollover 5-17s after the OUTGOING plan's
    projected end while a boundary-aligned seamless reload was still ARMED
    and settling (the dispatched durations sum underestimates real playout,
    and the daemon observes the engine's boundary commit a few seconds after
    the fact). Re-establishing there read the daemon's dispatch record --
    still the OLD plan, since ``_record_dispatched_plan`` runs at settlement
    -- and dated it from "now", so the tuple ended one old plan-length in the
    future. When the settlement landed, that poisoned ``previous_end_at`` fed
    the deferred-start rule and dated the INCOMING plan's horizon a whole
    plan too late: no rollover was issued for it, the plan ran to EOS, and
    the channel took the 20s planned restart on every rollover.

    Fix: while the daemon reports a pending settlement -- whoever issued the
    reload: this pass's own rollover, a slate replan, or an operator reload
    (hostile review round 2, finding 1: ``should_defer_switch`` defers ANY
    ON_AIR reload, so they all share the overrun window) -- a past
    ``plan_end_at`` is a settling switch, not the frozen-horizon lie: leave
    the tuple alone and wait, logging the wait once per
    ``_ROLLOVER_RETRY_WARN_INTERVAL_SECONDS`` (finding 3). Belt: the
    deferred-start anchor is only honoured within the OUTGOING plan's own
    scaled rollover lead (``_rollover_min_lead_seconds``, finding 2) of
    ``now``, so a poisoned ``previous_end_at`` -- which sits about one
    outgoing-plan-length ahead -- can never push a horizon a whole plan out,
    however short the plan.

    The outgoing plan is (500, 100): ends _NOW+600, trigger at _NOW+480. The
    incoming rollover plan is (200, 100). The SHORT cases use a (60, 40)
    outgoing plan (100s, under the flat 120s lead) and the same shape
    incoming."""

    _OUTGOING = (500.0, 100.0)
    _INCOMING = (200.0, 100.0)
    _SHORT = (60.0, 40.0)

    def _on_air(self, store: InMemoryEgressStore, channel_id: str, proof_event_id: str) -> None:
        store.write_state(
            EgressStateRow(
                channel_id=channel_id,
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id=proof_event_id,
                updated_at=_NOW,
            )
        )

    def _armed_and_settling(
        self,
    ) -> tuple[
        InMemoryEgressStore, _SettlingHorizonAwareDaemon, ChannelAutomationService, dict[str, float]
    ]:
        """Establish the outgoing plan, fire its boundary-aligned rollover, and
        have the daemon report that reload as armed and still settling. The
        returned ``clock`` is the service's injected monotonic reading, in
        seconds since ``_NOW``; advance it alongside ``now`` so the dispatch
        cadence floor sees real elapsed time."""
        clock = {"now": 0.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-1")
        daemon = _SettlingHorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", self._OUTGOING, False)
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_segments(cid, self._INCOMING),
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )
        service.run_once(now=_NOW)
        assert service._plan_horizon["public"][1] == _NOW + timedelta(seconds=600)
        clock["now"] = 480.0
        service.run_once(now=_NOW + timedelta(seconds=480))
        assert _pending_actions(store, "public") == ["reload"]
        store.pop_pending_commands("public")
        assert "public" in service._rollover_issued
        daemon.pending_channels.add("public")
        return store, daemon, service, clock

    def test_a_past_horizon_is_left_alone_while_the_daemon_is_still_settling(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store, _daemon, service, clock = self._armed_and_settling()
        before = service._plan_horizon["public"]

        # 10s past the projected end (soak: 5-17s), reload still settling.
        clock["now"] = 610.0
        with caplog.at_level(logging.WARNING, logger="civiccast.egress.automation"):
            service.run_once(now=_NOW + timedelta(seconds=610))

        assert service._plan_horizon["public"] == before  # tuple untouched
        assert "public" in service._rollover_issued
        assert _pending_actions(store, "public") == []
        assert not [r for r in caplog.records if "was stale" in r.getMessage()]

    def test_the_incoming_plan_is_then_dated_from_now_not_from_the_poisoned_end(
        self,
    ) -> None:
        store, daemon, service, clock = self._armed_and_settling()
        clock["now"] = 610.0
        service.run_once(now=_NOW + timedelta(seconds=610))

        # The settlement commits: the daemon drops the pending flag, the proof
        # event changes, and the dispatch record now describes the incoming
        # deferred plan (as EgressDaemon._commit_reload_settlement does).
        daemon.pending_channels.discard("public")
        self._on_air(store, "public", "ev-2")
        daemon.dispatched["public"] = ("ev-2", self._INCOMING, True)
        settled_at = _NOW + timedelta(seconds=612)
        clock["now"] = 612.0
        service.run_once(now=settled_at)

        proof_id, plan_end_at, last_segment_start_at, planned_seconds = service._plan_horizon[
            "public"
        ]
        assert proof_id == "ev-2"
        assert planned_seconds == 300.0
        assert plan_end_at == settled_at + timedelta(seconds=300)  # NOT previous_end + 300
        assert last_segment_start_at == settled_at + timedelta(seconds=200)
        assert "public" not in service._rollover_issued
        assert _pending_actions(store, "public") == []

        # And the incoming plan's own rollover fires at its real trigger
        # (min(_NOW+812, _NOW+912-120) = _NOW+792), not a plan-length late.
        clock["now"] = 791.0
        service.run_once(now=_NOW + timedelta(seconds=791))
        assert _pending_actions(store, "public") == []
        clock["now"] = 793.0
        service.run_once(now=_NOW + timedelta(seconds=793))
        assert _pending_actions(store, "public") == ["reload"]

    def test_the_stale_branch_still_runs_once_the_settlement_is_discarded_without_a_commit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store, daemon, service, clock = self._armed_and_settling()
        clock["now"] = 610.0
        service.run_once(now=_NOW + timedelta(seconds=610))

        # The worker exits: the daemon discards the pending settlement with no
        # proof-event change (``_discard_pending_reload_settlement``). The
        # tuple is now a genuinely stale horizon again -- item 78 fix 2
        # applies exactly as before.
        daemon.pending_channels.discard("public")
        clock["now"] = 614.0
        with caplog.at_level(logging.WARNING, logger="civiccast.egress.automation"):
            service.run_once(now=_NOW + timedelta(seconds=614))

        assert [r for r in caplog.records if "retry for public dispatched" in r.getMessage()]
        assert "public" in service._rollover_issued
        # The original boundary remains authoritative and the failed settlement
        # is retried instead of re-dating the old dispatch from this tick.
        assert service._plan_horizon["public"][1] == _NOW + timedelta(seconds=600)
        assert _pending_actions(store, "public") == ["reload"]

    def test_a_deferred_anchor_more_than_a_lead_time_ahead_is_not_honoured(self) -> None:
        """Belt: even if a poisoned ``previous_end_at`` reaches the deferred-start
        rule, it is ignored unless it lies within ``_ROLLOVER_MIN_LEAD_SECONDS``
        of ``now`` -- the horizon is dated from ``now`` instead."""
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-1")
        daemon = _SettlingHorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", self._OUTGOING, False)  # ends _NOW+600
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_segments(cid, self._INCOMING),
            settings=ChannelAutomationSettings(),
        )
        service.run_once(now=_NOW)

        # A deferred plan takes air while the tracked end is 500s ahead --
        # more than the 120s lead floor, so not a boundary this switch could
        # have been aligned to.
        self._on_air(store, "public", "ev-2")
        daemon.dispatched["public"] = ("ev-2", self._INCOMING, True)
        now = _NOW + timedelta(seconds=100)
        service.run_once(now=now)

        assert service._plan_horizon["public"][1] == now + timedelta(seconds=300)

    def test_a_short_plan_operator_reload_overrun_is_left_alone_while_settling(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Hostile review round 2, finding 1 (the reviewer's reproduction):
        a 100s outgoing plan, a reload this pass did NOT issue
        (``_rollover_issued`` is empty -- an operator-queued or slate reload,
        both deferred to the boundary by ``should_defer_switch``), and the
        outgoing leg overrunning its projected end while the daemon reports
        the reload still settling. With the ``_rollover_issued`` conjunct in
        the guard the stale branch fired here, re-read the OLD dispatch
        record, and poisoned the horizon exactly as in the soak."""
        clock = {"now": 0.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-1")
        daemon = _SettlingHorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", self._SHORT, False)
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_segments(cid, self._SHORT),
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )
        service.run_once(now=_NOW)
        before = service._plan_horizon["public"]
        assert before[1] == _NOW + timedelta(seconds=100)
        assert "public" not in service._rollover_issued

        # An operator reload is armed and settling; the outgoing leg runs 10s
        # past its projected end.
        daemon.pending_channels.add("public")
        clock["now"] = 110.0
        with caplog.at_level(logging.INFO, logger="civiccast.egress.automation"):
            service.run_once(now=_NOW + timedelta(seconds=110))

        assert service._plan_horizon["public"] == before  # tuple untouched
        assert "public" not in service._rollover_issued
        assert _pending_actions(store, "public") == []
        assert not [r for r in caplog.records if "was stale" in r.getMessage()]
        assert [r for r in caplog.records if "still settling; waiting" in r.getMessage()]

        # The settlement commits: the incoming deferred plan is dated from
        # now, and its own rollover fires at its real trigger
        # (min(settled+60, settled+100-50) = settled+50), not a plan late.
        daemon.pending_channels.discard("public")
        self._on_air(store, "public", "ev-2")
        daemon.dispatched["public"] = ("ev-2", self._SHORT, True)
        settled_at = _NOW + timedelta(seconds=112)
        clock["now"] = 112.0
        service.run_once(now=settled_at)
        proof_id, plan_end_at, last_segment_start_at, planned_seconds = service._plan_horizon[
            "public"
        ]
        assert proof_id == "ev-2"
        assert planned_seconds == 100.0
        assert plan_end_at == settled_at + timedelta(seconds=100)
        assert last_segment_start_at == settled_at + timedelta(seconds=60)
        assert _pending_actions(store, "public") == []
        clock["now"] = 161.0
        service.run_once(now=_NOW + timedelta(seconds=161))
        assert _pending_actions(store, "public") == []
        clock["now"] = 163.0
        service.run_once(now=_NOW + timedelta(seconds=163))
        assert _pending_actions(store, "public") == ["reload"]

    def test_a_short_plan_deferred_anchor_is_bounded_by_the_outgoing_plans_own_lead(
        self,
    ) -> None:
        """Hostile review round 2, finding 2: the belt must scale with the
        OUTGOING plan. A poisoned anchor sits about one outgoing-plan-length
        ahead -- 98s here for a 100s plan -- which the flat 120s bound
        honoured (belt inert for every plan under 120s). The bound is now
        ``_rollover_min_lead_seconds(100) = 50s``: the poison is rejected and
        a genuine boundary a few seconds ahead is still honoured."""
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", "ev-1")
        daemon = _SettlingHorizonAwareDaemon(live_channels={"public"})
        daemon.dispatched["public"] = ("ev-1", self._SHORT, False)  # ends _NOW+100
        service = ChannelAutomationService(
            store,
            daemon,
            lambda cid: _plan_with_segments(cid, self._SHORT),
            settings=ChannelAutomationSettings(),
        )
        service.run_once(now=_NOW)

        # Poisoned: a deferred plan takes air with the tracked end 98s ahead
        # (under the flat 120s, over the plan's own 50s lead).
        self._on_air(store, "public", "ev-2")
        daemon.dispatched["public"] = ("ev-2", self._SHORT, True)
        now = _NOW + timedelta(seconds=2)
        service.run_once(now=now)
        assert service._plan_horizon["public"][1] == now + timedelta(seconds=100)

        # Genuine: the next deferred plan is observed 5s before the tracked
        # end (a boundary this switch really was aligned to) -- honoured.
        self._on_air(store, "public", "ev-3")
        daemon.dispatched["public"] = ("ev-3", self._SHORT, True)
        tracked_end = service._plan_horizon["public"][1]
        service.run_once(now=tracked_end - timedelta(seconds=5))
        assert service._plan_horizon["public"][1] == tracked_end + timedelta(seconds=100)

    def test_the_settling_wait_is_logged_once_per_interval_not_once_per_tick(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Hostile review round 2, finding 3: the wait is visible in the log
        (the old "was stale" WARNING is how the soak bug was found) but
        rate-limited per ``_ROLLOVER_RETRY_WARN_INTERVAL_SECONDS`` (60s), not
        emitted on every ~2s poll tick."""
        store, _daemon, service, clock = self._armed_and_settling()

        def waiting_lines() -> list[logging.LogRecord]:
            return [
                r
                for r in caplog.records
                if r.levelno >= logging.INFO and "still settling; waiting" in r.getMessage()
            ]

        with caplog.at_level(logging.DEBUG, logger="civiccast.egress.automation"):
            for elapsed in range(610, 670, 2):  # 30 ticks inside one interval
                clock["now"] = float(elapsed)
                service.run_once(now=_NOW + timedelta(seconds=elapsed))
            assert len(waiting_lines()) == 1
            assert "past due by 10s" in waiting_lines()[0].getMessage()
            # The next tick past the interval logs once more, then goes quiet.
            clock["now"] = 671.0
            service.run_once(now=_NOW + timedelta(seconds=671))
            clock["now"] = 673.0
            service.run_once(now=_NOW + timedelta(seconds=673))
        assert len(waiting_lines()) == 2
        assert service._plan_horizon["public"][1] == _NOW + timedelta(seconds=600)
        assert _pending_actions(store, "public") == []


class TestRolloverRetryDispatchTimestampIsClearedWithTheIssuedLatch:
    """Coordinator review, round 4, item 2(a): ``_rollover_retry_dispatched_at``
    must be cleared everywhere ``_rollover_issued`` itself is discarded (a
    landed rollover, or a discarded stale horizon) -- not only when the
    channel leaves the air. Without this, a stale retry timestamp left over
    from an earlier, already-resolved rollover sequence could incorrectly
    suppress the very FIRST retry of whatever rollover comes next, even
    though nothing about that next sequence has actually violated the 60s
    floor. These tests assert directly on the cleared state (white-box,
    deterministic) rather than threading a full establish/dispatch/retry/
    land timing chain through black-box behavior -- see
    ``TestRolloverRetryFloorMeasuredFromTheLastRetryNotTheOriginalDispatch``'s
    churn test for a black-box demonstration of the same clearing in a
    realistic relaunch scenario."""

    def _on_air(self, store: InMemoryEgressStore, channel_id: str, *, proof_event_id: str) -> None:
        store.write_state(
            EgressStateRow(
                channel_id=channel_id,
                state="ON_AIR",
                current_source_label="Council Meeting",
                current_proof_event_id=proof_event_id,
                updated_at=_NOW,
            )
        )

    def test_cleared_when_a_fresh_plan_takes_air(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", proof_event_id="ev-1")
        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda cid: _plan_with_duration(cid, 1800.0),
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)  # establish: ends far in the future
        assert _pending_actions(store, "public") == []

        # Simulate an earlier, already-resolved rollover retry sequence for
        # THIS channel having left a timestamp behind.
        service._rollover_retry_dispatched_at["public"] = service._monotonic()

        # A fresh proof event lands -- "a fresh plan just took air".
        self._on_air(store, "public", proof_event_id="ev-2")
        service.run_once(now=_NOW)

        assert service._rollover_retry_dispatched_at.get("public") is None

    def test_cleared_when_the_tracked_horizon_is_discarded_as_stale(self) -> None:
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", proof_event_id="ev-1")
        service = ChannelAutomationService(
            store,
            _FakeDaemon(live_channels={"public"}),
            lambda cid: _plan_with_duration(cid, 100.0),
            settings=ChannelAutomationSettings(),
        )

        service.run_once(now=_NOW)  # establish: ends at _NOW+100
        assert _pending_actions(store, "public") == []

        service._rollover_retry_dispatched_at["public"] = service._monotonic()

        # The wall clock jumps far past the tracked plan's own end -- the
        # horizon is stale and must be discarded/re-established (see
        # TestStaleRolloverHorizonIsReestablishedNotDispatchedAgainst above).
        far_future = _NOW + timedelta(seconds=5000)
        service.run_once(now=far_future)

        assert service._rollover_retry_dispatched_at.get("public") is None


class TestRolloverRetryRespectsAFloorAndAYoungWorkerGuard:
    """Item 78 fix 2 (second half): the B2 "reload never landed" retry path
    used to be fully exempt from any dispatch-cadence floor at all
    (deliberately -- "recovery, not cadence"), which meant a worker stuck
    crash-relaunching right after every reload got hit with an unthrottled
    re-arm about one second after every relaunch. Two independent guards now
    apply on the retry path only (the ordinary D45 per-plan cadence floor
    stays exempt on retry, as before): a flat minimum gap between retry
    dispatches, and a floor under how young the channel's current worker pid
    may be before another synchronous ``SourcePreparer.prepare`` is thrown at
    it."""

    def _on_air(
        self, store: InMemoryEgressStore, channel_id: str, *, proof_event_id: str, pid: int | None
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

    def test_the_floor_constants_match_the_spec(self) -> None:
        assert ChannelAutomationService._ROLLOVER_RETRY_MIN_INTERVAL_SECONDS == 60.0
        assert ChannelAutomationService._ROLLOVER_RETRY_MIN_WORKER_AGE_SECONDS == 60.0

    def test_retry_is_deferred_until_a_freshly_relaunched_worker_settles(self) -> None:
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", proof_event_id="ev-1", pid=111)
        daemon = _FakeDaemon(live_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)  # establishes the horizon; pid 111 first tracked
        later = _NOW + timedelta(seconds=35)
        service.run_once(now=later)  # dispatches the reload
        assert _pending_actions(store, "public") == ["reload"]

        # It never lands, AND (independently) the worker crashed and
        # relaunched with a NEW pid around the same time -- the process id
        # changed but the daemon has not confirmed a new source yet (the
        # proof event is unchanged).
        self._on_air(store, "public", proof_event_id="ev-1", pid=222)
        clock["now"] += 50.0  # past the 45s ISSUED_TIMEOUT
        service.run_once(now=later)
        # pid 222 has only been alive an instant by our tracking -- far
        # short of the 60s worker-age floor -- so the retry is deferred.
        assert _pending_actions(store, "public") == []
        assert calls["n"] == 2  # no extra prepare call while deferred

        # Once the pid has aged past the floor, the retry goes through.
        clock["now"] += 65.0
        service.run_once(now=later)
        assert _pending_actions(store, "public") == ["reload"]

    def test_an_unknown_pid_never_blocks_the_retry_indefinitely(self) -> None:
        """A bare test double / a state row with no pid information at all
        must never be treated as "just relaunched forever" -- see
        ``test_rollover_retries_once_if_the_dispatched_reload_never_lands``
        in ``TestPlanRollover``, which exercises this exact shape (pid always
        ``None``) and must keep passing unchanged."""
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        self._on_air(store, "public", proof_event_id="ev-1", pid=None)
        daemon = _FakeDaemon(live_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_duration("public", 40.0 if calls["n"] == 1 else 400.0)

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        service.run_once(now=_NOW)
        later = _NOW + timedelta(seconds=35)
        service.run_once(now=later)
        assert _pending_actions(store, "public") == ["reload"]

        clock["now"] += 50.0  # past the 45s timeout (the retry-interval floor is a
        # no-op here: this is the FIRST retry, so _rollover_retry_dispatched_at
        # has nothing recorded yet to measure a gap from)
        service.run_once(now=later)
        assert _pending_actions(store, "public") == ["reload"]


class TestRolloverRetryFloorMeasuredFromTheLastRetryNotTheOriginalDispatch:
    """Coordinator review, round 2, item 4: ``_ROLLOVER_RETRY_MIN_INTERVAL_SECONDS``
    was previously measured from ``_rollover_dispatched_at`` (the ORIGINAL
    dispatch) -- dead code, since the retry branch is only ever entered
    after ``_ROLLOVER_ISSUED_TIMEOUT_SECONDS`` (45s) has already elapsed
    since that same origin, so a floor <= 45s measured from it can never
    bind. It is now measured from ``_rollover_retry_dispatched_at`` (the
    last RETRY specifically), making it a real storm limiter: a channel
    stuck retrying repeatedly is capped to one retry per
    ``_ROLLOVER_RETRY_MIN_INTERVAL_SECONDS`` (60s), not one every time the
    45s timeout re-arms."""

    def test_a_worker_relaunching_faster_than_its_own_trigger_delay_gets_no_rollover_during_the_churn(
        self,
    ) -> None:
        """Coordinator review, round 3, item F (round-4 doc correction, item
        2(c)): models a REAL worker relaunch -- a non-ON_AIR tick
        (``STARTING``) followed by ON_AIR again with a FRESH proof event and
        a new pid -- not merely a changed pid on an otherwise-unbroken
        ON_AIR/proof-event stream (the round-2 shape, which really only
        exercised the worker-pid-age guard in isolation and never any of
        the horizon-reset cleanup branches at all).

        What this test actually demonstrates: the "not ON_AIR" branch's
        horizon reset (``_plan_horizon``/``_rollover_issued``/
        ``_rollover_dispatched_at``/``_rollover_retry_dispatched_at`` all
        cleared, present since round 2 -- the ``STARTING`` tick on every
        relaunch cycle) -- NOT round 3 item E's specific addition (clearing
        ``_rollover_retry_dispatched_at`` on the "fresh plan just took air"
        and "stale horizon" branches). Because the ``STARTING`` tick always
        runs immediately before the landing tick in this exact sequence,
        the "fresh plan took air" branch's own clearing is REDUNDANT here --
        the value is already ``None`` by the time it runs. Item E's two
        clearings are covered directly (and non-redundantly) by
        ``TestRolloverRetryDispatchTimestampIsClearedWithTheIssuedLatch``
        above instead.

        The plan here is two segments (40s, 200s): its boundary-aligned
        trigger is 40s after establishment (the last segment's own start),
        deliberately longer than the ~25s a horizon gets to live between one
        relaunch's landing and the next relaunch's ``STARTING`` tick (30s
        apart). Every relaunch therefore wipes the tracked horizon via the
        "not ON_AIR" branch before it ever gets far enough to reach its own
        trigger. This is the exact degrade mode named in the CHANGELOG: a
        worker that relaunches faster than a rollover's own trigger delay
        never keeps a horizon tracked long enough to fire one at all while
        it keeps doing that -- not a hang, just a reversion to the pre-
        item-78 shape (the channel reaches its own EOS/crash cycle and the
        daemon's own crash back-off, untouched by this fix, owns the
        restart). Once the worker stabilizes (stops relaunching), the
        rollover mechanism gets a real chance to run: one dispatch at the
        trigger and, since it never lands here either, retries capped to
        the 60s floor -- the same steady-state behavior the round-2 version
        of this test proved."""
        clock = {"now": 1000.0}
        store = InMemoryEgressStore()
        store.upsert_config(_config("public"))
        daemon = _FakeDaemon(live_channels={"public"})
        calls = {"n": 0}

        def provider(_cid: str) -> EgressSourcePlan:
            calls["n"] += 1
            return _plan_with_segments(
                "public", (40.0, 200.0), source_ref_prefix=f"call{calls['n']}"
            )

        service = ChannelAutomationService(
            store,
            daemon,
            provider,
            settings=ChannelAutomationSettings(),
            monotonic=lambda: clock["now"],
        )

        def starting(pid: int) -> None:
            store.write_state(
                EgressStateRow(
                    channel_id="public",
                    state="STARTING",
                    current_proof_event_id=None,
                    updated_at=_NOW,
                    pid=pid,
                )
            )

        def on_air(pid: int, proof_event_id: str) -> None:
            store.write_state(
                EgressStateRow(
                    channel_id="public",
                    state="ON_AIR",
                    current_source_label="Council Meeting",
                    current_proof_event_id=proof_event_id,
                    updated_at=_NOW,
                    pid=pid,
                )
            )

        # Wall clock advances in lockstep with the monotonic clock (unlike
        # every OTHER test in this module, which pins "now" to a single
        # fixed instant) -- required here so the plan's own 40s-out trigger
        # is ever actually reached by wall clock at all once the churn ends;
        # a pinned "now" would leave ``now < trigger_at`` true forever.
        wall_now = _NOW
        on_air(pid=1, proof_event_id="ev-1")
        service.run_once(now=wall_now)  # establish: 240s plan, trigger 40s out

        dispatch_times: list[float] = []
        pid_counter = 1
        proof_counter = 1
        tick = 0.0
        step = 5.0
        last_relaunch_tick = 0.0
        pending_landing = False
        # Bounded to stay within the ONE plan lifetime (240s) the LAST
        # relaunch (landing by tick 250) establishes -- long enough to
        # observe the churn producing nothing, then the original dispatch
        # and several retries, short enough that the item-2 stale-horizon
        # guard never re-establishes a SECOND lifetime and confuses the
        # gap-spacing assertions below with another original-dispatch reset.
        while tick < 460.0:
            clock["now"] += step
            wall_now = wall_now + timedelta(seconds=step)
            tick += step
            if pending_landing:
                # The relaunch "lands" the next tick after STARTING -- back
                # ON_AIR with a fresh proof event, same new pid.
                on_air(pid=pid_counter, proof_event_id=f"ev-{proof_counter}")
                pending_landing = False
            elif tick <= 250.0 and tick - last_relaunch_tick >= 30.0:
                pid_counter += 1
                proof_counter += 1
                last_relaunch_tick = tick
                starting(pid=pid_counter)
                pending_landing = True
            service.run_once(now=wall_now)
            if _pending_actions(store, "public") == ["reload"]:
                dispatch_times.append(clock["now"])

        # No rollover ever got far enough to dispatch during the churn (every
        # relaunch wipes the horizon well before its own 40s trigger delay).
        assert all(t > 1000.0 + 250.0 for t in dispatch_times), dispatch_times
        # The original dispatch plus several retries land once the worker
        # stabilizes -- not a vacuous "zero dispatches ever" result.
        assert len(dispatch_times) >= 3, dispatch_times
        gaps = [b - a for a, b in pairwise(dispatch_times)]
        # The FIRST gap is original-dispatch -> first-retry, legitimately
        # governed by the 45s ISSUED_TIMEOUT alone (the interval floor is a
        # no-op for a channel's very first retry -- nothing recorded yet to
        # measure a gap from). Every gap AFTER that is retry -> retry, and
        # must respect the 60s interval floor -- the storm limiter this fix
        # exists to make meaningful.
        assert gaps[0] >= 45.0, gaps
        assert all(gap >= 60.0 for gap in gaps[1:]), gaps
