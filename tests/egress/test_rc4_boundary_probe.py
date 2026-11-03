# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""RC4: the boundary probe's +1s margin must not become an aired JIP offset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from civiccast.egress.automation import ChannelAutomationService
from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.errors import SourcePrepareError
from civiccast.egress.models import (
    CanonicalProfile,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
)
from civiccast.egress.source_plan import (
    ScheduleSourcePlanProvider,
    re_resolve_program_at_occurrence_start,
)
from civiccast.egress.store import InMemoryEgressStore
from civiccast.schedule.models import ScheduleItemResponse, StaffAssetRow


def _config() -> EgressConfig:
    return EgressConfig(
        channel_id="gov",
        enabled=True,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri="build/gov.ts")],
    )


def _scheduled_item(
    asset_id: str,
    at: datetime,
    duration_seconds: int,
) -> ScheduleItemResponse:
    return ScheduleItemResponse(
        id=uuid4(),
        asset_id=asset_id,
        asset_title=asset_id,
        channel_id="gov",
        mode="premiere",
        state="published",
        scheduled_at=at,
        duration_seconds=duration_seconds,
        notes=None,
        created_at=at - timedelta(days=1),
    )


def _asset(
    path: Path,
    asset_id: str,
    *,
    trim_in: float = 10.0,
    trim_out: float = 48.0,
    duration_seconds: int = 48,
) -> StaffAssetRow:
    path.write_bytes(b"fixture")
    return StaffAssetRow(
        asset_id=asset_id,
        title=asset_id,
        state="validated",
        file_path=str(path),
        duration_seconds=duration_seconds,
        trim_in_seconds=trim_in,
        trim_out_seconds=trim_out,
    )


def _provider(
    items: list[ScheduleItemResponse],
    assets: dict[str, StaffAssetRow],
) -> ScheduleSourcePlanProvider:
    return ScheduleSourcePlanProvider(
        schedule_items_provider=lambda _channel_id: items,
        asset_resolver=lambda asset_id: assets[asset_id],
        media_duration_resolver=lambda _path: None,
    )


def _program_plan(
    tmp_path: Path,
    *,
    channel_id: str = "gov",
    occurrence_id: str | None = "occ",
    source_ref: str | None = "src",
    occurrence_start: datetime | None = None,
    duration_seconds: float = 10.0,
) -> EgressSourcePlan:
    path = tmp_path / f"{uuid4().hex}.ts"
    path.write_text("fixture", encoding="utf-8")
    return EgressSourcePlan(
        channel_id=channel_id,
        segments=[
            EgressSourceSegment(
                label="program",
                path=str(path),
                duration_seconds=duration_seconds,
                kind="program",
                source_ref=source_ref,
                schedule_occurrence_id=occurrence_id,
                schedule_occurrence_start=occurrence_start,
            )
        ],
    )


def test_boundary_probe_re_resolves_at_occurrence_start_with_real_trim(
    tmp_path: Path,
) -> None:
    """Actual provider: +1s probe is 1s/37s; actual start is 10s/38s."""

    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    items = [
        _scheduled_item("tail", start - timedelta(seconds=5), 5),
        _scheduled_item("next", start, 38),
    ]
    assets = {
        "tail": _asset(tmp_path / "tail.mp4", "tail", trim_in=0, trim_out=5, duration_seconds=5),
        "next": _asset(tmp_path / "next.mp4", "next", trim_in=10, trim_out=48),
    }
    provider = _provider(items, assets)
    probed_at = start + timedelta(seconds=1)
    probed = provider.plan_at("gov", probed_at)

    assert probed is not None
    assert probed.segments[0].schedule_occurrence_id == str(items[1].id)
    assert probed.segments[0].source_ref == "next"
    assert probed.segments[0].inpoint_seconds == 11
    assert probed.segments[0].duration_seconds == 37

    calls: list[tuple[datetime, dict[str, object]]] = []

    def boundary(channel_id: str, at: datetime, **kwargs: object):
        calls.append((at, dict(kwargs)))
        return provider.plan_at(channel_id, at, **kwargs)

    resolved = re_resolve_program_at_occurrence_start(
        "gov",
        boundary,
        probed,
        probed_at=probed_at,
        true_boundary=start,
        provider_kwargs={"min_plan_seconds": 690.0},
    )

    assert calls == [(start, {"min_plan_seconds": 690.0})]
    assert resolved is not None
    segment = resolved.segments[0]
    assert segment.schedule_occurrence_id == str(items[1].id)
    assert segment.source_ref == "next"
    assert segment.schedule_occurrence_start == start
    assert segment.inpoint_seconds == 10
    assert segment.duration_seconds == 38


def test_daemon_tail_callsite_adopts_occurrence_start_requery(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    item = _scheduled_item("next", start, 38)
    provider = _provider(
        [item],
        {"next": _asset(tmp_path / "next.mp4", "next", trim_in=10, trim_out=48)},
    )
    calls: list[datetime] = []

    def boundary(channel_id: str, at: datetime, **kwargs: object):
        calls.append(at)
        return provider.plan_at(channel_id, at, **kwargs)

    store = InMemoryEgressStore()
    store.upsert_config(_config())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: None,
        boundary_source_plan_provider=boundary,
    )
    tail = _program_plan(
        tmp_path,
        occurrence_id=None,
        source_ref="tail",
        occurrence_start=None,
        duration_seconds=5.0,
    )

    outcome = daemon._resolve_schedule_tail(
        "gov",
        tail,
        _config(),
        resolved_at=start - timedelta(seconds=5),
    )

    assert outcome.slate is False
    assert calls == [start + timedelta(seconds=1), start]
    segment = outcome.plan.segments[0]
    assert segment.schedule_occurrence_start == start
    assert segment.inpoint_seconds == 10
    assert segment.duration_seconds == 38


def test_automation_tail_callsite_adopts_occurrence_start_requery(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    item = _scheduled_item("next", start, 38)
    provider = _provider(
        [item],
        {"next": _asset(tmp_path / "next.mp4", "next", trim_in=10, trim_out=48)},
    )
    calls: list[datetime] = []

    def boundary(channel_id: str, at: datetime, **kwargs: object):
        calls.append(at)
        return provider.plan_at(channel_id, at, **kwargs)

    service = object.__new__(ChannelAutomationService)
    service._boundary_source_plan_provider = boundary
    fresh = _program_plan(
        tmp_path,
        occurrence_id=None,
        source_ref="tail",
        occurrence_start=None,
        duration_seconds=5.0,
    )

    replacement = service._resolve_rollover_tail(
        "gov",
        start - timedelta(seconds=5),
        fresh,
    )

    assert replacement is not None
    assert calls == [start + timedelta(seconds=1), start]
    segment = replacement.segments[0]
    assert segment.schedule_occurrence_start == start
    assert segment.inpoint_seconds == 10
    assert segment.duration_seconds == 38


def test_helper_leaves_missing_provenance_untouched(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    plan = _program_plan(
        tmp_path,
        occurrence_id=None,
        occurrence_start=start,
    )
    calls: list[datetime] = []

    def boundary(channel_id: str, at: datetime, **kwargs: object):
        calls.append(at)
        return plan

    out = re_resolve_program_at_occurrence_start(
        "gov",
        boundary,
        plan,
        probed_at=start + timedelta(seconds=1),
        true_boundary=start,
    )

    assert out is plan
    assert calls == []


def test_helper_never_normalizes_an_older_current_occurrence_jip(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    plan = _program_plan(
        tmp_path,
        occurrence_id="occ",
        source_ref="src",
        occurrence_start=start - timedelta(seconds=2),
    )
    calls: list[datetime] = []

    def boundary(channel_id: str, at: datetime, **kwargs: object):
        calls.append(at)
        return plan

    out = re_resolve_program_at_occurrence_start(
        "gov",
        boundary,
        plan,
        probed_at=start + timedelta(seconds=1),
        true_boundary=start,
    )

    assert out is plan
    assert calls == []


@pytest.mark.parametrize(
    "mismatch",
    ["channel", "occurrence", "source", "start", "none", "shorter", "exception"],
)
def test_helper_retains_original_for_unadoptable_requery(
    tmp_path: Path,
    mismatch: str,
) -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    original = _program_plan(
        tmp_path,
        occurrence_id="occ",
        source_ref="src",
        occurrence_start=start,
        duration_seconds=12.0,
    )
    replacement_kwargs: dict[str, object] = {
        "duration_seconds": 20.0,
        "occurrence_id": "occ",
        "source_ref": "src",
        "occurrence_start": start,
    }
    if mismatch == "channel":
        replacement_kwargs["channel_id"] = "other"
    elif mismatch == "occurrence":
        replacement_kwargs["occurrence_id"] = "other-occ"
    elif mismatch == "source":
        replacement_kwargs["source_ref"] = "other-src"
    elif mismatch == "start":
        replacement_kwargs["occurrence_start"] = start + timedelta(seconds=0.5)
    elif mismatch == "shorter":
        replacement_kwargs["duration_seconds"] = 11.0
    calls: list[datetime] = []

    def boundary(channel_id: str, at: datetime, **kwargs: object):
        calls.append(at)
        if mismatch == "exception":
            raise SourcePrepareError("no")
        if mismatch == "none":
            return None
        return _program_plan(tmp_path, **replacement_kwargs)

    out = re_resolve_program_at_occurrence_start(
        "gov",
        boundary,
        original,
        probed_at=start + timedelta(seconds=1),
        true_boundary=start,
    )

    assert out is original
    assert len(calls) == 1
