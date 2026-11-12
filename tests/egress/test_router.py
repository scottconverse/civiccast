# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import civiccast.egress.models
import civiccast.schedule.models  # noqa: F401
from civiccast.app import create_app
from civiccast.db import Base, bind_engine, reset_engine
from civiccast.egress import router as egress_router
from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.encoder_strategy import EncoderStartRequest, EncoderStartResult
from civiccast.egress.errors import SourcePrepareError
from civiccast.egress.models import (
    EgressCaptionProofSample,
    EgressCommand,
    EgressHealthSample,
    EgressProofEvent,
    EgressSourcePlan,
    EgressSourceSegment,
    EgressStateRow,
)
from civiccast.egress.operator_state import operator_state_projection
from civiccast.egress.router import get_egress_store, get_egress_work_dir
from civiccast.egress.store import PostgresEgressStore
from civiccast.live.router import get_live_session_store

pytestmark = pytest.mark.usefixtures("deterministic_staff_token")


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    bind_engine(eng)
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        reset_engine()
        eng.dispose()


@pytest.fixture
def store(engine: Engine) -> PostgresEgressStore:
    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=engine) as session:
            yield session

    return PostgresEgressStore(factory)


@pytest.fixture
def client(store: PostgresEgressStore) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_egress_store] = lambda: store
    # No live SESSION is ever on air in these tests: /api/public/live/current
    # then falls through to the egress state, which is what they assert.
    app.dependency_overrides[get_live_session_store] = lambda: SimpleNamespace(
        list_sessions=lambda **_kwargs: []
    )
    with TestClient(app, headers={"Authorization": "Bearer operator-token-a"}) as c:
        yield c


def _config_payload(channel_id: str = "gov") -> dict[str, object]:
    return {
        "channel_id": channel_id,
        "enabled": True,
        "slate_message": "CivicCast is preparing the channel.",
        "sinks": [
            {
                "kind": "srt",
                "label": "Headend",
                "uri": "srt://headend.example:9000",
                "secret_ref": "EGRESS_SRT_PASSPHRASE",
            }
        ],
    }


def test_staff_egress_storage_reports_logs_commands_and_low_disk(
    client: TestClient,
    store: PostgresEgressStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "gov" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "gst-worker.stdout.log").write_bytes(b"stdout")
    (log_dir / "gst-worker.stdout.log.1").write_bytes(b"old")
    (log_dir / "other.log").write_bytes(b"ignore")
    client.app.dependency_overrides[get_egress_work_dir] = lambda: tmp_path
    monkeypatch.setattr(
        egress_router.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=90, free=10 * 1024**3),
    )
    now = datetime.now(UTC)
    store.enqueue_commands(
        [
            EgressCommand(
                channel_id="gov",
                action="start",
                issued_at=now,
                issued_by="test",
                command_id="consumed",
            ),
            EgressCommand(
                channel_id="other",
                action="start",
                issued_at=now,
                issued_by="test",
                command_id="pending",
            ),
        ]
    )
    assert len(store.pop_pending_commands("gov")) == 1

    response = client.get("/api/staff/egress/storage")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "worker_log_bytes": 9,
        "worker_log_file_count": 2,
        "command_history_rows": 2,
        "pending_commands": 1,
        "command_table_bytes": None,
        "work_volume_free_bytes": 10 * 1024**3,
        "low_disk_warning": True,
        "low_disk_threshold_bytes": 20 * 1024**3,
    }


def test_staff_egress_config_round_trip(client: TestClient) -> None:
    r = client.put("/api/staff/egress/channels/gov/config", json=_config_payload())
    assert r.status_code == 200, r.text
    assert r.json()["sinks"][0]["label"] == "Headend"

    r = client.get("/api/staff/egress/channels/gov/config")
    assert r.status_code == 200
    assert r.json()["channel_id"] == "gov"


def test_staff_egress_config_accepts_hls_sink(client: TestClient, hls_root: Path) -> None:
    """DEFECT A: this exact repro (PUT an hls sink, 200 OK) used to crash the
    channel on the following ``start``. It still returns 200 — hls is now a
    genuinely supported sink kind for the default GStreamer engine — but see
    ``test_hls_relay.py``/``test_gst_bridge.py`` for proof it no longer
    crashes the channel, not just that the API accepts it. The folder must be
    inside the served root (round-2 delta review, BLOCKER 2)."""
    payload = _config_payload() | {
        "sinks": [{"kind": "hls", "label": "Web", "uri": str(hls_root / "gov")}]
    }
    r = client.put("/api/staff/egress/channels/gov/config", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["sinks"][0]["kind"] == "hls"


def test_staff_egress_config_allows_rtmp_on_default_gstreamer_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RTMP is implemented by the default GStreamer engine and saves normally."""
    monkeypatch.delenv("CIVICCAST_EGRESS_ENGINE", raising=False)  # ensure GStreamer default
    payload = _config_payload() | {
        "sinks": [{"kind": "rtmp", "label": "Restream", "uri": "rtmp://example.com/live"}]
    }
    r = client.put("/api/staff/egress/channels/gov/config", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["sinks"][0]["kind"] == "rtmp"

    # The accepted config must reach the durable row, not merely return 200.
    r = client.get("/api/staff/egress/channels/gov/config")
    assert r.status_code == 200, r.text
    assert r.json()["sinks"][0]["kind"] == "rtmp"


def test_staff_egress_graphics_overlay_round_trip(client: TestClient) -> None:
    """A-3: the operator's lower-third toggle + text persist through the
    dedicated GET/PUT endpoint and flow back to the full config GET (proving
    it lands on the same durable EgressConfig row the playout-graph builder
    reads, not a side channel)."""
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )

    # Default is off/blank before any operator ever sets it.
    r = client.get("/api/staff/egress/channels/gov/graphics-overlay")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "channel_id": "gov",
        "graphics_overlay_enabled": False,
        "graphics_overlay_lower_third_text": "",
    }

    r = client.put(
        "/api/staff/egress/channels/gov/graphics-overlay",
        json={
            "graphics_overlay_enabled": True,
            "graphics_overlay_lower_third_text": "Town Council - Live",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "channel_id": "gov",
        "graphics_overlay_enabled": True,
        "graphics_overlay_lower_third_text": "Town Council - Live",
    }

    # Get-after-set round trip via the dedicated endpoint...
    r = client.get("/api/staff/egress/channels/gov/graphics-overlay")
    assert r.status_code == 200
    assert r.json()["graphics_overlay_enabled"] is True
    assert r.json()["graphics_overlay_lower_third_text"] == "Town Council - Live"

    # ...and via the full channel config, proving it's the same durable row.
    r = client.get("/api/staff/egress/channels/gov/config")
    assert r.status_code == 200
    assert r.json()["graphics_overlay_enabled"] is True
    assert r.json()["graphics_overlay_lower_third_text"] == "Town Council - Live"

    # Take it back off air.
    r = client.put(
        "/api/staff/egress/channels/gov/graphics-overlay",
        json={
            "graphics_overlay_enabled": False,
            "graphics_overlay_lower_third_text": "Town Council - Live",
        },
    )
    assert r.status_code == 200
    assert r.json()["graphics_overlay_enabled"] is False


def test_staff_egress_graphics_overlay_404_before_config_exists(client: TestClient) -> None:
    r = client.get("/api/staff/egress/channels/never-configured/graphics-overlay")
    assert r.status_code == 404

    r = client.put(
        "/api/staff/egress/channels/never-configured/graphics-overlay",
        json={"graphics_overlay_enabled": True, "graphics_overlay_lower_third_text": "x"},
    )
    assert r.status_code == 404


def test_staff_egress_graphics_overlay_rejects_control_characters(client: TestClient) -> None:
    """MINOR fix (2026-08-30 audit): mirrors ``ndi_relay_name``/``sdi_relay_device``'s
    ``clean_relay_identifier`` control-char rule for the lower-third text -- rejected
    at save time so a poisoned string can never reach
    ``graphics_overlay_leg_from_config``'s rendered banner PNG.

    This exercises the PUT endpoint itself, not just the ``EgressConfig`` model: the
    endpoint applies the payload via ``EgressConfig.model_copy(update=...)``, which
    does NOT re-run ``EgressConfig``'s own field validators -- so the request body
    model (``GraphicsOverlayUpdateRequest``) needs the identical check, or a control
    character would sail straight past the model-level validator through this
    endpoint's actual write path (the real regression this test targets)."""
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )

    r = client.put(
        "/api/staff/egress/channels/gov/graphics-overlay",
        json={
            "graphics_overlay_enabled": True,
            "graphics_overlay_lower_third_text": "Council\x07Room",
        },
    )
    assert r.status_code == 422, r.text
    assert "control characters" in r.text

    # And the config must NOT have been persisted (a 422 that still wrote the row
    # would be the same accept-then-crash gap with extra steps).
    r = client.get("/api/staff/egress/channels/gov/graphics-overlay")
    assert r.status_code == 200
    assert r.json()["graphics_overlay_lower_third_text"] == ""


def test_staff_egress_config_rtmp_allowed_on_ffmpeg_concat_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validation is engine-aware: rtmp is fully implemented by the
    ffmpeg-concat engine's RtmpSink, so it must not be rejected there."""
    monkeypatch.setenv("CIVICCAST_EGRESS_ENGINE", "ffmpeg-concat")
    payload = _config_payload() | {
        "sinks": [{"kind": "rtmp", "label": "Restream", "uri": "rtmp://example.com/live"}]
    }
    r = client.put("/api/staff/egress/channels/gov/config", json=payload)
    assert r.status_code == 200, r.text


def test_staff_egress_channel_list_and_detail(
    client: TestClient,
    store: PostgresEgressStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Council meeting",
            current_proof_event_id="proof-1",
            updated_at=now,
            pid=1234,
        )
    )
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now,
            state="ON_AIR",
            sink_connected={"Headend": True},
            encoder_fps=30.0,
            seconds_on_air=12,
            caption_status="not-verified",
        )
    )

    r = client.get("/api/staff/egress/channels")

    assert r.status_code == 200
    assert r.json()[0]["channel_id"] == "gov"
    assert r.json()[0]["sink_count"] == 1
    assert r.json()[0]["state"]["state"] == "ON_AIR"
    assert r.json()[0]["latest_health"]["seconds_on_air"] == 12

    r = client.get("/api/staff/egress/channels/gov")

    assert r.status_code == 200
    assert r.json()["config"]["channel_id"] == "gov"
    assert r.json()["state"]["current_source_label"] == "Council meeting"
    assert r.json()["latest_health"]["caption_status"] == "not-verified"


@pytest.mark.parametrize(("alive", "expected_pid"), [(False, None), (True, 4321), (None, None)])
def test_staff_state_projects_dead_worker_pid(
    client: TestClient,
    store: PostgresEgressStore,
    monkeypatch: pytest.MonkeyPatch,
    alive: bool | None,
    expected_pid: int | None,
) -> None:
    store.write_state(
        EgressStateRow(channel_id="gov", state="ON_AIR", updated_at=datetime.now(UTC), pid=4321)
    )
    monkeypatch.setattr(
        egress_router,
        "operator_state_projection",
        lambda state: operator_state_projection(state, pid_is_alive=lambda _pid: alive),
    )
    response = client.get("/api/staff/egress/channels/gov/state")
    assert response.status_code == 200
    assert response.json()["pid"] == expected_pid


def test_staff_state_keeps_normal_stopped_row(
    client: TestClient, store: PostgresEgressStore
) -> None:
    store.write_state(
        EgressStateRow(channel_id="gov", state="STOPPED", updated_at=datetime.now(UTC), pid=None)
    )
    response = client.get("/api/staff/egress/channels/gov/state")
    assert response.status_code == 200
    assert response.json()["state"] == "STOPPED"
    assert response.json()["pid"] is None


def test_public_egress_now_is_viewer_safe(
    client: TestClient,
    store: PostgresEgressStore,
) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Council meeting",
            current_proof_event_id="proof-1",
            updated_at=now,
            pid=1234,
            last_error="internal detail",
        )
    )
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now,
            state="ON_AIR",
            sink_connected={"Headend": True},
            encoder_fps=30.0,
            seconds_on_air=12,
            caption_status="not-verified",
        )
    )

    r = client.get("/api/public/egress/channels/gov/now", headers={})

    assert r.status_code == 200
    body = r.json()
    assert body["channel_id"] == "gov"
    assert body["state"] == "ON_AIR"
    assert body["current_source_label"] == "Council meeting"
    assert body["updated_at"].startswith("2026-06-05T12:00:00")
    assert body["caption_status"] == "not-verified"
    assert body["seconds_on_air"] == 12
    assert "pid" not in body
    assert "current_proof_event_id" not in body
    assert "sink_connected" not in body
    assert "last_error" not in body


def test_staff_egress_config_path_body_mismatch_is_422(client: TestClient) -> None:
    r = client.put("/api/staff/egress/channels/gov/config", json=_config_payload("schools"))

    assert r.status_code == 422
    assert "does not match" in r.json()["detail"]


def test_staff_egress_command_is_queued_for_daemon(
    client: TestClient,
    store: PostgresEgressStore,
) -> None:
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    r = client.post(
        "/api/staff/egress/channels/gov/commands",
        json={"action": "start"},
    )

    assert r.status_code == 202
    body = r.json()
    assert body["queued"] is True
    assert body["command"]["command_id"].startswith("egress-")
    assert body["command"]["issued_by"] == "operator-token-a"
    assert [cmd.command_id for cmd in store.pop_pending_commands("gov")] == [
        body["command"]["command_id"]
    ]


def test_staff_egress_start_is_refused_with_a_reason_when_no_config_exists(
    client: TestClient,
    store: PostgresEgressStore,
) -> None:
    # beta.5 walkthrough F-29: Start on an unconfigured channel used to be
    # accepted (202) and silently dropped by the daemon; state stayed Stopped.
    r = client.post(
        "/api/staff/egress/channels/gov/commands",
        json={"action": "start"},
    )

    assert r.status_code == 409
    assert r.json()["detail"] == (
        "No outgoing-feed configuration for gov. Apply a headend preset or the local "
        "rehearsal preset first."
    )
    assert store.pop_pending_commands("gov") == []


def test_staff_egress_start_is_refused_when_config_is_disabled(client: TestClient) -> None:
    payload = _config_payload()
    payload["enabled"] = False
    assert client.put("/api/staff/egress/channels/gov/config", json=payload).status_code == 200

    r = client.post("/api/staff/egress/channels/gov/commands", json={"action": "start"})

    assert r.status_code == 409
    assert "disabled" in r.json()["detail"]


def test_staff_egress_stop_is_still_accepted_without_config(
    client: TestClient,
    store: PostgresEgressStore,
) -> None:
    # Only start needs a config; stop/drain on an unconfigured channel is a
    # harmless no-op for the daemon and must stay reachable.
    r = client.post("/api/staff/egress/channels/gov/commands", json={"action": "stop"})

    assert r.status_code == 202
    assert [cmd.action for cmd in store.pop_pending_commands("gov")] == ["stop"]


def test_staff_egress_command_rejects_client_supplied_actor(client: TestClient) -> None:
    r = client.post(
        "/api/staff/egress/channels/gov/commands",
        json={"action": "start", "issued_by": "spoofed-operator"},
    )

    assert r.status_code == 422
    assert "Extra inputs are not permitted" in r.text


def test_staff_egress_state_and_health_reads(
    client: TestClient,
    store: PostgresEgressStore,
) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Council meeting",
            current_proof_event_id="proof-1",
            updated_at=now,
            pid=1234,
        )
    )
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now,
            state="ON_AIR",
            sink_connected={"Headend": True},
            encoder_fps=30.0,
            seconds_on_air=5,
            caption_status="not-verified",
        )
    )

    r = client.get("/api/staff/egress/channels/gov/state")
    assert r.status_code == 200
    assert r.json()["state"] == "ON_AIR"

    r = client.get("/api/staff/egress/channels/gov/health")
    assert r.status_code == 200
    assert r.json()[0]["seconds_on_air"] == 5
    assert r.json()[0]["caption_status"] == "not-verified"


def test_staff_egress_schema_currency_reports_current_and_drift(
    client: TestClient,
    store: PostgresEgressStore,
) -> None:
    from civiccast.egress.schema_currency import current_schema_version

    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)

    # No sample yet → current (nothing to drift against).
    r = client.get("/api/staff/egress/channels/gov/schema-currency")
    assert r.status_code == 200
    body = r.json()
    assert body["is_current"] is True
    assert body["sample_schema_version"] is None

    # A sample at the running schema version → current, with the proof-churn count.
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now,
            state="ON_AIR",
            schema_version=current_schema_version(),
            proof_events_appended_since_last_sample=7,
        )
    )
    body = client.get("/api/staff/egress/channels/gov/schema-currency").json()
    assert body["is_current"] is True
    assert body["sample_schema_version"] == current_schema_version()
    assert body["proof_events_appended_since_last_sample"] == 7

    # A newer sample written by a DIFFERENT schema version → drift flagged.
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=datetime(2026, 6, 5, 12, 1, tzinfo=UTC),
            state="ON_AIR",
            schema_version=current_schema_version() + 1,
            proof_events_appended_since_last_sample=0,
        )
    )
    body = client.get("/api/staff/egress/channels/gov/schema-currency").json()
    assert body["is_current"] is False
    assert body["sample_schema_version"] == current_schema_version() + 1

    # Negative direction: a NEWER current-version sample (e.g. after the operator
    # upgrades the code) must clear the badge to green even though an OLDER drift
    # sample still sits in the table — the surface reflects the LATEST sample only,
    # not "any sample ever drifted" (a regression that kept it red would ship green).
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=datetime(2026, 6, 5, 12, 2, tzinfo=UTC),
            state="ON_AIR",
            schema_version=current_schema_version(),
            proof_events_appended_since_last_sample=0,
        )
    )
    body = client.get("/api/staff/egress/channels/gov/schema-currency").json()
    assert body["is_current"] is True
    assert body["sample_schema_version"] == current_schema_version()


def test_staff_egress_proof_reads_recent_events(
    client: TestClient,
    store: PostgresEgressStore,
) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    store.append_proof_event(
        EgressProofEvent(
            event_id="proof-1",
            observed_at=now,
            channel_id="gov",
            state="ON_AIR",
            source_label="Council meeting",
            source_path="prepared/council.ts",
            source_ref="asset-council",
            proof_boundary="civiccast-egress-handoff-boundary",
            machine_summary="Council meeting went to air.",
        )
    )

    r = client.get("/api/staff/egress/channels/gov/proof")

    assert r.status_code == 200
    body = r.json()
    assert body[0]["event_id"] == "proof-1"
    assert body[0]["source_ref"] == "asset-council"
    assert body[0]["proof_boundary"] == "civiccast-egress-handoff-boundary"


def test_staff_egress_routes_return_503_without_store() -> None:
    app = create_app()
    with TestClient(app, headers={"Authorization": "Bearer operator-token-a"}) as bare:
        r = bare.get("/api/staff/egress/channels/gov/config")

    assert r.status_code == 503
    assert "Durable storage is not ready" in r.json()["detail"]


def test_headend_profiles_listing_is_static_and_citable(client: TestClient) -> None:
    # Cable automation CA-6: the registry is generic product surface.
    r = client.get("/api/staff/egress/headend-profiles")

    assert r.status_code == 200
    profiles = {entry["profile_id"]: entry for entry in r.json()}
    assert "comcast-mtd-sd" in profiles
    assert "telvue-hypercaster-ip" in profiles
    for entry in profiles.values():
        assert entry["source_urls"], entry["profile_id"]
        assert entry["not_claimed"], entry["profile_id"]


def test_apply_headend_profile_creates_a_fresh_config(client: TestClient) -> None:
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={
            "profile_id": "comcast-mtd-sd",
            "destination_uri": "udp://239.255.0.1:5000",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()["config"]
    assert r.json()["on_air_effect"] == "next_start"
    # Issue #324: the default GStreamer engine cannot air MPEG-2 video; the apply keeps H.264.
    assert body["canonical_profile"]["video_codec"] == "h264"
    sink = body["sinks"][0]
    assert sink["kind"] == "udp-ts"
    muxrate_idx = sink["extra_output_args"].index("-muxrate")
    assert sink["extra_output_args"][muxrate_idx + 1] == "3750k"

    persisted = client.get("/api/staff/egress/channels/gov/config").json()
    assert persisted["sinks"][0]["kind"] == "udp-ts"


def test_apply_headend_profile_keeps_existing_sinks_when_asked(client: TestClient) -> None:
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={
            "profile_id": "generic-udp-spts",
            "destination_uri": "udp://10.0.0.9:5000",
            "muxrate_kbps": 9000,
            "keep_existing_sinks": True,
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()["config"]
    kinds = sorted(sink["kind"] for sink in body["sinks"])
    assert kinds == ["srt", "udp-ts"]
    headend = next(sink for sink in body["sinks"] if sink["kind"] == "udp-ts")
    muxrate_idx = headend["extra_output_args"].index("-muxrate")
    assert headend["extra_output_args"][muxrate_idx + 1] == "9000k"


def test_apply_headend_profile_404_unknown_and_422_destination_mismatch(
    client: TestClient,
) -> None:
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "nope", "destination_uri": "udp://239.0.0.1:5000"},
    )
    assert r.status_code == 404

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "comcast-mtd-sd", "destination_uri": "udp://10.0.0.5:5000"},
    )
    assert r.status_code == 422
    assert "multicast" in r.json()["detail"]


def test_apply_headend_profile_503_without_store() -> None:
    app = create_app()
    with TestClient(app, headers={"Authorization": "Bearer operator-token-a"}) as bare:
        r = bare.post(
            "/api/staff/egress/channels/gov/config/headend-profile",
            json={
                "profile_id": "generic-udp-spts",
                "destination_uri": "udp://10.0.0.9:5000",
            },
        )

    assert r.status_code == 503


def _udp_ts_config_payload(channel_id: str = "gov") -> dict[str, object]:
    return {
        "channel_id": channel_id,
        "enabled": True,
        "slate_message": "CivicCast is preparing the channel.",
        "sinks": [
            {
                "kind": "udp-ts",
                "label": "Cable headend",
                "uri": "udp://239.255.0.1:5000",
                "extra_output_args": ["-muxrate", "8000k"],
            }
        ],
    }


def test_compliance_probe_endpoint_runs_via_seam(client: TestClient) -> None:
    # Cable automation CA-7: the prober seam is overridable so the API
    # contract is testable without TSDuck or a live stream.
    from datetime import UTC, datetime

    from civiccast.egress.compliance import ComplianceCheck, ComplianceProbeResult
    from civiccast.egress.router import get_compliance_prober

    calls: list[tuple[str, int]] = []

    def fake_prober(config, seconds):  # type: ignore[no-untyped-def]
        calls.append((config.channel_id, seconds))
        return ComplianceProbeResult(
            channel_id=config.channel_id,
            destination="udp://239.255.0.1:5000",
            probed_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
            seconds=seconds,
            expected_muxrate_kbps=8000,
            checks=[ComplianceCheck(check="ts-sync", status="pass", detail="ok")],
            verdict="pass",
        )

    client.app.dependency_overrides[get_compliance_prober] = lambda: fake_prober  # type: ignore[attr-defined]
    assert (
        client.put(
            "/api/staff/egress/channels/gov/config", json=_udp_ts_config_payload()
        ).status_code
        == 200
    )

    r = client.post("/api/staff/egress/channels/gov/compliance-probe", json={"seconds": 15})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "pass"
    assert calls == [("gov", 15)]


def test_compliance_probe_endpoint_rejects_channels_without_udp_ts(
    client: TestClient,
) -> None:
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )

    r = client.post("/api/staff/egress/channels/gov/compliance-probe", json={})

    assert r.status_code == 422
    assert "udp-ts" in r.json()["detail"]


def test_compliance_probe_endpoint_404_without_config(client: TestClient) -> None:
    r = client.post("/api/staff/egress/channels/nope/compliance-probe", json={})
    assert r.status_code == 404


def test_headend_readiness_reports_tsduck_and_last_probes(client: TestClient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from civiccast.egress.compliance import ComplianceProbeResult
    from civiccast.egress.router import get_egress_work_dir

    client.app.dependency_overrides[get_egress_work_dir] = lambda: tmp_path  # type: ignore[attr-defined]
    assert (
        client.put(
            "/api/staff/egress/channels/gov/config", json=_udp_ts_config_payload()
        ).status_code
        == 200
    )
    (tmp_path / "gov").mkdir()
    (tmp_path / "gov" / "compliance-last.json").write_text(
        ComplianceProbeResult(
            channel_id="gov", destination="udp://239.255.0.1:5000", verdict="pass"
        ).model_dump_json(),
        encoding="utf-8",
    )

    r = client.get("/api/staff/egress/headend-readiness")

    assert r.status_code == 200, r.text
    body = r.json()
    assert "installed" in body["tsduck"]
    channels = {entry["channel_id"]: entry for entry in body["channels"]}
    assert channels["gov"]["destination"] == "udp://239.255.0.1:5000"
    assert channels["gov"]["last_probe"]["verdict"] == "pass"


def test_headend_device_probe_endpoint(client: TestClient) -> None:
    from datetime import UTC, datetime

    from civiccast.egress.compliance import DevicePortProbe, DeviceProbeResult
    from civiccast.egress.router import get_device_prober

    def fake_device_prober(host, ports):  # type: ignore[no-untyped-def]
        return DeviceProbeResult(
            host=host,
            ports=[DevicePortProbe(port=p, reachable=p == 443) for p in ports],
            any_reachable=True,
            probed_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
        )

    client.app.dependency_overrides[get_device_prober] = lambda: fake_device_prober  # type: ignore[attr-defined]

    r = client.post(
        "/api/staff/egress/headend-device-probe",
        json={"host": "headend.example", "ports": [80, 443]},
    )

    assert r.status_code == 200, r.text
    assert r.json()["any_reachable"] is True


def test_ndi_readiness_reports_byo_state_and_relay_statuses(
    client: TestClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    # Issue #116: honest BYO posture - no NDI-capable ffmpeg configured on
    # this host, plus whatever relays the automation pass is supervising.
    from civiccast.egress.ndi_relay import (
        NdiRelayStatus,
        clear_relay_statuses,
        set_relay_status,
    )

    monkeypatch.delenv("CIVICCAST_NDI_FFMPEG", raising=False)
    clear_relay_statuses()
    set_relay_status(
        NdiRelayStatus(
            channel_id="public",
            ndi_name="CivicCast Public",
            state="blocked",
            next_step="Set CIVICCAST_NDI_FFMPEG ...",
        )
    )

    r = client.get("/api/staff/egress/ndi-readiness")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["byo_ffmpeg_configured"] is False
    assert "CIVICCAST_NDI_FFMPEG" in body["next_step"]
    relays = {entry["channel_id"]: entry for entry in body["relays"]}
    assert relays["public"]["state"] == "blocked"
    clear_relay_statuses()


def test_sdi_readiness_reports_byo_state_and_relay_statuses(
    client: TestClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    # Issue #117: honest BYO posture - no DeckLink-capable ffmpeg configured
    # on this host, plus whatever relays the automation pass is supervising.
    from civiccast.egress.sdi_relay import (
        SdiRelayStatus,
        clear_relay_statuses,
        set_relay_status,
    )

    monkeypatch.delenv("CIVICCAST_SDI_FFMPEG", raising=False)
    clear_relay_statuses()
    set_relay_status(
        SdiRelayStatus(
            channel_id="public",
            device="DeckLink Mini Monitor 4K",
            state="blocked",
            next_step="Set CIVICCAST_SDI_FFMPEG ...",
        )
    )

    r = client.get("/api/staff/egress/sdi-readiness")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["byo_ffmpeg_configured"] is False
    assert "CIVICCAST_SDI_FFMPEG" in body["next_step"]
    relays = {entry["channel_id"]: entry for entry in body["relays"]}
    assert relays["public"]["state"] == "blocked"
    assert relays["public"]["device"] == "DeckLink Mini Monitor 4K"
    clear_relay_statuses()


# --- S11b per-sink loudness plan --------------------------------------------


def _loudness_role_app(scopes: tuple[str, ...] | None, store: PostgresEgressStore) -> Any:
    """Bare app mounting the egress staff router with a chosen operator identity
    so the real require_any_role gate runs (mirrors control_room's role tests)."""
    from fastapi import FastAPI, Request, Response

    from civiccast.auth.models import OperatorIdentity
    from civiccast.egress.router import staff_router

    app = FastAPI()

    @app.middleware("http")
    async def _ident(request: Request, call_next: Any) -> Response:
        if scopes is not None:
            request.state.operator_identity = OperatorIdentity(
                operator_id="dana", operator_display_name="Dana", scopes=scopes
            )
        return await call_next(request)

    app.include_router(staff_router)
    app.dependency_overrides[get_egress_store] = lambda: store
    return app


def _loudness_payload(channel_id: str = "gov") -> dict[str, object]:
    return {
        "channel_id": channel_id,
        "enabled": True,
        "slate_message": "CivicCast is preparing the channel.",
        "loudness_target_lufs": -16.0,
        "sinks": [
            {
                "kind": "udp-ts",
                "label": "Cable",
                "uri": "udp://239.255.0.1:5000",
                "loudness_regime": "atsc-a85",
            },
            {
                "kind": "srt",
                "label": "CDN",
                "uri": "srt://cdn.example:9000",
                "loudness_regime": "streaming",
            },
        ],
    }


def test_staff_egress_config_write_accepts_per_sink_loudness(client: TestClient) -> None:
    # S11b: the existing config write accepts the per-sink loudness fields and
    # round-trips them through GET config.
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_loudness_payload()).status_code
        == 200
    )
    body = client.get("/api/staff/egress/channels/gov/config").json()
    by_label = {s["label"]: s for s in body["sinks"]}
    assert by_label["Cable"]["loudness_regime"] == "atsc-a85"
    assert by_label["Cable"]["eas_tone_strip_enabled"] is True
    assert by_label["CDN"]["loudness_regime"] == "streaming"


def test_loudness_plan_resolves_per_sink_targets(client: TestClient) -> None:
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_loudness_payload()).status_code
        == 200
    )
    r = client.get("/api/staff/egress/channels/gov/loudness-plan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["baseline_target_lufs"] == -16.0
    by_label = {s["label"]: s for s in body["sinks"]}
    assert by_label["Cable"]["effective_target_lufs"] == -24.0
    assert by_label["Cable"]["standard_label"] == "ATSC A/85 -24 LKFS (CALM Act)"
    assert by_label["Cable"]["requires_reencode"] is True
    assert by_label["CDN"]["effective_target_lufs"] == -16.0
    assert by_label["CDN"]["requires_reencode"] is False


def test_loudness_plan_unknown_channel_is_404(client: TestClient) -> None:
    assert client.get("/api/staff/egress/channels/ghost/loudness-plan").status_code == 404


def test_loudness_plan_forbidden_for_records_clerk(store: PostgresEgressStore) -> None:
    app = _loudness_role_app(("records_clerk",), store)
    with TestClient(app) as c:
        assert c.get("/api/staff/egress/channels/gov/loudness-plan").status_code == 403


def test_loudness_plan_allowed_for_support_admin(
    client: TestClient, store: PostgresEgressStore
) -> None:
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_loudness_payload()).status_code
        == 200
    )
    app = _loudness_role_app(("support_admin",), store)
    with TestClient(app) as c:
        assert c.get("/api/staff/egress/channels/gov/loudness-plan").status_code == 200


def test_loudness_plan_503_without_store() -> None:
    app = create_app()
    with TestClient(app, headers={"Authorization": "Bearer operator-token-a"}) as bare:
        r = bare.get("/api/staff/egress/channels/gov/loudness-plan")
    assert r.status_code == 503


# --- S11a caption decode-back status + proofs ---------------------------------


def _seed_caption_proof(
    store: PostgresEgressStore, *, status: str, caption_status: str, sampled_at: datetime
) -> None:
    store.append_caption_proof_sample(
        EgressCaptionProofSample(
            channel_id="gov",
            sampled_at=sampled_at,
            status=status,  # type: ignore[arg-type]
            caption_status=caption_status,  # type: ignore[arg-type]
            mode="cea-708",
            decoder_name="ffmpeg-subcc",
            expected_cue_count=2,
            decoded_cue_count=2 if status == "PASS" else 0,
            matched_cue_count=2 if status == "PASS" else 0,
            proof_boundary="egress-caption-embed-to-emitted-stream-decode-back",
        )
    )


def test_caption_status_not_verified_without_proof(client: TestClient) -> None:
    r = client.get("/api/staff/egress/channels/gov/caption-status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caption_status"] == "not-verified"
    assert body["latest"] is None


def test_caption_status_on_for_fresh_pass(client: TestClient, store: PostgresEgressStore) -> None:
    _seed_caption_proof(store, status="PASS", caption_status="on", sampled_at=datetime.now(UTC))
    r = client.get("/api/staff/egress/channels/gov/caption-status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caption_status"] == "on"
    assert body["latest"]["status"] == "PASS"


def test_caption_proofs_returns_recent_samples(
    client: TestClient, store: PostgresEgressStore
) -> None:
    now = datetime.now(UTC)
    _seed_caption_proof(store, status="FAIL", caption_status="not-verified", sampled_at=now)
    _seed_caption_proof(
        store, status="PASS", caption_status="on", sampled_at=now + timedelta(seconds=30)
    )
    r = client.get("/api/staff/egress/channels/gov/caption-proofs?limit=10")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["status"] for s in body] == ["PASS", "FAIL"]


def test_caption_status_forbidden_for_records_clerk(store: PostgresEgressStore) -> None:
    app = _loudness_role_app(("records_clerk",), store)
    with TestClient(app) as c:
        assert c.get("/api/staff/egress/channels/gov/caption-status").status_code == 403
        assert c.get("/api/staff/egress/channels/gov/caption-proofs").status_code == 403


def test_caption_status_allowed_for_meeting_operator(store: PostgresEgressStore) -> None:
    app = _loudness_role_app(("meeting_operator",), store)
    with TestClient(app) as c:
        assert c.get("/api/staff/egress/channels/gov/caption-status").status_code == 200


def test_caption_status_503_without_store() -> None:
    app = create_app()
    with TestClient(app, headers={"Authorization": "Bearer operator-token-a"}) as bare:
        r = bare.get("/api/staff/egress/channels/gov/caption-status")
    assert r.status_code == 503


def test_staff_repair_gstreamer_endpoint_returns_recovery_outcome(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tier-5 operator recovery endpoint delegates to
    ``trigger_gstreamer_repair`` and surfaces its outcome. The endpoint is
    backend-only in this change (the operator-console button is a documented
    TODO); the native repair itself is exercised in
    tests/egress/test_gstreamer_degrade_recovery.py."""
    import civiccast.native.gstreamer_repair as repair

    def _fake_trigger() -> repair.RepairTrigger:
        return repair.RepairTrigger(
            triggered=True,
            healthy=False,
            installer_binary=None,
            argv=("x",),
            pid=777,
            detail="re-stage launched",
            remedy="restage-launched",
        )

    monkeypatch.setattr(repair, "trigger_gstreamer_repair", _fake_trigger)

    r = client.post("/api/staff/egress/repair-gstreamer")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["triggered"] is True
    assert body["closure_healthy"] is False
    assert body["remedy"] == "restage-launched"
    assert body["pid"] == 777


def test_apply_local_hls_profile_via_api_with_no_destination(client: TestClient) -> None:
    # First-time operator path: no egress config yet, no destination typed.
    # The placeholder file sink the fresh path seeds is replaced by ONE hls sink
    # under the station's egress work dir, and the channel comes back enabled.
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "local-rehearsal-hls"},
    )

    assert r.status_code == 200, r.text
    body = r.json()["config"]
    assert r.json()["on_air_effect"] == "next_start"
    assert body["enabled"] is True
    assert [sink["kind"] for sink in body["sinks"]] == ["hls"]
    assert body["sinks"][0]["label"] == "Web preview (HLS)"
    assert body["sinks"][0]["uri"].replace("\\", "/").endswith("/live-hls/gov")


def test_apply_local_hls_profile_via_api_rejects_network_destination(
    client: TestClient,
) -> None:
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "local-rehearsal-hls", "destination_uri": "udp://239.0.0.1:5000"},
    )
    assert r.status_code == 422
    assert "local folder" in r.json()["detail"]


def test_apply_cable_profile_via_api_still_requires_destination(client: TestClient) -> None:
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "generic-udp-spts"},
    )
    assert r.status_code == 422
    assert "udp" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Review round 2, BLOCKER 2 + round-2 delta review, BLOCKER 1: applying a
# headend preset only upserted the config row, and the daemon reads a
# channel's sinks only when it BUILDS a pipeline. Round 2 queued a ``reload``
# -- but on the default GStreamer engine a reload never rebuilds sinks
# (``gst/engine.py`` applies only the program source and the graphics
# overlay), while ``daemon.py`` still started the HLS relay for the new sink,
# so residents got a player pointed at a manifest that never existed. The
# route now restarts (``stop`` + ``start``) a channel standing by on its
# slate, leaves a program on air alone and SAYS SO (``on_air_effect``), and
# ``/api/public/live/current`` advertises a manifest only when the playlist
# exists. These tests assert the OUTCOME through a real ``EgressDaemon``
# draining the same store, not the presence of a queue row.
# ---------------------------------------------------------------------------


def _write_egress_state(store: PostgresEgressStore, channel_id: str, state: str) -> None:
    store.write_state(
        EgressStateRow(
            channel_id=channel_id,
            state=state,  # type: ignore[arg-type]
            updated_at=datetime.now(UTC),
        )
    )


class _RouterFakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0


class _SeamlessReloadStrategy:
    """Stands in for ``GstPlayoutStrategy``: content-reload capable, and --
    exactly like the real engine -- a reload re-applies the PROGRAM SOURCE
    only. The sink set a pipeline carries is the one it was STARTED with, so
    ``starts[-1].config.sinks`` is what is on air."""

    name = "fake-gst-seamless"
    supports_live_swap = True
    supports_content_reload = True

    def __init__(self) -> None:
        self.starts: list[EncoderStartRequest] = []
        self.processes: list[_RouterFakeProcess] = []
        self.reloads: list[EncoderStartRequest] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        process = _RouterFakeProcess(pid=5000 + len(self.processes))
        self.processes.append(process)
        self.starts.append(request)
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
        self.reloads.append(request)
        channel_dir = work_dir / channel_id
        channel_dir.mkdir(parents=True, exist_ok=True)
        (channel_dir / "reload-status.json").write_text(
            json.dumps({"id": command_id or "x", "result": "applied"}), encoding="utf-8"
        )
        return True


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


def _daemon_on_slate(
    store: PostgresEgressStore, tmp_path: Path
) -> tuple[EgressDaemon, _SeamlessReloadStrategy]:
    """A daemon over the API's own store whose only program source is missing,
    so every start lands on the fallback slate."""
    strategy = _SeamlessReloadStrategy()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "work",
        source_plan_provider=lambda _channel_id: (_ for _ in ()).throw(
            SourcePrepareError("Nothing scheduled.")
        ),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,
    )
    return daemon, strategy


@pytest.fixture
def hls_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "hls-root"
    root.mkdir()
    monkeypatch.setenv("CIVICCAST_LIVE_HLS_ROOT", str(root))
    return root


def test_web_preview_preset_on_a_standing_by_channel_lands_on_air_via_restart(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path, hls_root: Path
) -> None:
    # The beta.5 walkthrough: the channel idles on its slate on a UDP headend
    # preset, the operator enables the web preview. Outcome asserted: the
    # pipeline the daemon is running afterwards was BUILT with the hls sink.
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    daemon, strategy = _daemon_on_slate(store, tmp_path)
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime.now(UTC),
            issued_by="operator",
            command_id="cmd-start",
        )
    )
    assert daemon.process_once("gov") == 1
    before = store.read_state("gov")
    assert before is not None and before.state == "FALLBACK_SLATE"
    assert [sink.kind for sink in strategy.starts[0].config.sinks] == ["srt"]

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "local-rehearsal-hls", "keep_existing_sinks": True},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["on_air_effect"] == "restart_queued"
    assert "restarted" in body["on_air_detail"]
    assert [sink["kind"] for sink in body["config"]["sinks"]] == ["srt", "hls"]

    # The daemon drains what the route queued: the slate worker is gone and
    # the replacement pipeline carries the new sink. No reload was involved.
    assert daemon.process_once("gov") == 2
    assert strategy.processes[0].terminated is True
    assert len(strategy.starts) == 2
    on_air_sinks = strategy.starts[1].config.sinks
    assert [sink.kind for sink in on_air_sinks] == ["srt", "hls"]
    assert Path(on_air_sinks[1].uri) == (hls_root / "gov").resolve()
    assert strategy.reloads == []
    after = store.read_state("gov")
    assert after is not None
    assert after.state == "FALLBACK_SLATE"
    assert after.pid == strategy.processes[1].pid
    assert store.pop_pending_commands("gov") == []

    # Once that pipeline writes its manifest, residents get it -- and not before.
    current = client.get("/api/public/live/current").json()
    assert current["state"] == "standing_by"
    assert current["manifest_url"] is None
    (hls_root / "gov").mkdir(parents=True, exist_ok=True)
    (hls_root / "gov" / "playlist.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    current = client.get("/api/public/live/current").json()
    assert current["manifest_url"] == "/media/live/gov/playlist.m3u8"


def test_web_preview_preset_on_a_program_on_air_is_honest_and_never_lies_to_residents(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path, hls_root: Path
) -> None:
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    daemon, strategy = _daemon_on_slate(store, tmp_path)
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Council meeting",
            updated_at=datetime.now(UTC),
        )
    )

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "local-rehearsal-hls", "keep_existing_sinks": True},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["on_air_effect"] == "restart_required"
    assert "Stop and then Start" in body["on_air_detail"]
    assert [sink["kind"] for sink in body["config"]["sinks"]] == ["srt", "hls"]
    # The route did not decide to cut a program: nothing queued, nothing started.
    assert daemon.process_once("gov") == 0
    assert strategy.starts == [] and strategy.reloads == []
    state = store.read_state("gov")
    assert state is not None and state.state == "ON_AIR"

    # Residents: on air, web preview configured but NOT serving -- never a
    # manifest URL that 404s.
    current = client.get("/api/public/live/current").json()
    assert current["state"] == "on_air_no_web_output"
    assert current["manifest_url"] is None
    assert current["reason"] == "HLS output configured but not serving yet"
    assert current["title"] == "Council meeting"


@pytest.mark.parametrize("state", [None, "STOPPED", "STOPPING", "DRAINING", "ERROR"])
def test_apply_headend_profile_on_a_dark_channel_reports_next_start_and_starts_nothing(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path, state: str | None
) -> None:
    # A start on a dark channel is an operator decision; the route only saves
    # the config and says when it lands.
    if state is not None:
        _write_egress_state(store, "gov", state)
    daemon, strategy = _daemon_on_slate(store, tmp_path)

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "generic-udp-spts", "destination_uri": "udp://10.0.0.9:5000"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["on_air_effect"] == "next_start"
    assert "next started" in r.json()["on_air_detail"]
    assert daemon.process_once("gov") == 0
    assert strategy.starts == []
    assert store.read_state("gov") is None if state is None else True


@pytest.mark.parametrize("state", ["STARTING", "TRANSITIONING"])
def test_apply_headend_profile_while_starting_or_handing_off_requires_a_restart(
    client: TestClient, store: PostgresEgressStore, state: str
) -> None:
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    _write_egress_state(store, "gov", state)

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "generic-udp-spts", "destination_uri": "udp://10.0.0.9:5000"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["on_air_effect"] == "restart_required"
    assert store.pop_pending_commands("gov") == []


def test_apply_headend_profile_twice_is_unchanged_and_queues_no_second_restart(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path
) -> None:
    # A real daemon runs the slate pipeline, so its health samples carry the
    # labels of the sinks the pipeline was BUILT with -- the evidence the route
    # needs before it may answer "unchanged" for a running channel.
    daemon, strategy = _daemon_on_slate(store, tmp_path)
    payload = {"profile_id": "generic-udp-spts", "destination_uri": "udp://10.0.0.9:5000"}
    first = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["on_air_effect"] == "next_start"
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime.now(UTC),
            issued_by="operator",
            command_id="cmd-start",
        )
    )
    assert daemon.process_once("gov") == 1
    assert [sink.kind for sink in strategy.starts[0].config.sinks] == ["udp-ts"]
    state = store.read_state("gov")
    assert state is not None and state.state == "FALLBACK_SLATE"

    second = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)

    assert second.status_code == 200, second.text
    assert second.json()["on_air_effect"] == "unchanged"
    assert "already delivering" in second.json()["on_air_detail"]
    assert second.json()["config"] == first.json()["config"]
    assert store.pop_pending_commands("gov") == []
    assert daemon.process_once("gov") == 0
    assert len(strategy.starts) == 1


def test_identical_apply_on_a_dark_channel_is_unchanged_and_says_so(
    client: TestClient, store: PostgresEgressStore
) -> None:
    payload = {"profile_id": "generic-udp-spts", "destination_uri": "udp://10.0.0.9:5000"}
    first = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["on_air_effect"] == "next_start"

    second = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)

    assert second.status_code == 200, second.text
    assert second.json()["on_air_effect"] == "unchanged"
    assert "not running" in second.json()["on_air_detail"]
    assert store.pop_pending_commands("gov") == []


def test_reapplying_the_preset_after_the_program_ends_puts_the_preview_on_air(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path, hls_root: Path
) -> None:
    """Review round 3 delta, MAJOR 2 -- the reviewer's executed sequence.

    1. A program is on air; the operator enables the web preview ->
       ``restart_required``, nothing queued (correct: never cut a program).
    2. The program ends; the SAME worker drops to ``FALLBACK_SLATE``. Its
       pipeline was built before the ``hls`` sink existed.
    3. The operator re-applies the same preset -- the obvious next move.

    Before the fix, step 3 short-circuited on the byte-identical config,
    answered ``unchanged`` / "nothing changed on air", queued nothing, and
    the preview stayed off air with no way out but a manual Stop/Start.
    """
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    daemon, strategy = _daemon_on_slate(store, tmp_path)
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime.now(UTC),
            issued_by="operator",
            command_id="cmd-start",
        )
    )
    assert daemon.process_once("gov") == 1
    # Step 1: the program commits to air on the running worker.
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Council meeting",
            updated_at=datetime.now(UTC),
            pid=strategy.processes[0].pid,
        )
    )
    payload = {"profile_id": "local-rehearsal-hls", "keep_existing_sinks": True}
    first = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["on_air_effect"] == "restart_required"
    assert store.pop_pending_commands("gov") == []

    # Step 2: the program ends; the same worker is back on its slate.
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="FALLBACK_SLATE",
            current_source_label="Fallback slate",
            updated_at=datetime.now(UTC),
            pid=strategy.processes[0].pid,
        )
    )
    assert daemon.process_once("gov") == 0  # a poll tick: health for the srt-only pipeline

    # Step 3: the operator applies the same preset again.
    second = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)

    assert second.status_code == 200, second.text
    assert second.json()["on_air_effect"] == "restart_queued", second.json()
    assert second.json()["config"] == first.json()["config"]
    # The daemon rebuilds the pipeline WITH the hls sink this time.
    assert daemon.process_once("gov") == 2
    assert strategy.processes[0].terminated is True
    assert [sink.kind for sink in strategy.starts[-1].config.sinks] == ["srt", "hls"]
    state = store.read_state("gov")
    assert state is not None and state.state == "FALLBACK_SLATE"

    # And now a third apply IS unchanged: the running pipeline carries the sink.
    third = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)
    assert third.json()["on_air_effect"] == "unchanged"
    assert store.pop_pending_commands("gov") == []


def _program_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "council.ts"
    source.write_text("council", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Council meeting",
                path=str(source),
                duration_seconds=3600,
                source_ref="asset-council",
            )
        ],
    )


def test_a_program_boundary_reload_does_not_make_an_unbuilt_sink_read_as_delivering(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path, hls_root: Path
) -> None:
    """Review round 4 delta, MAJOR 1 -- the reviewer's executed sequence.

    1. A program is on air; the operator enables the web preview ->
       ``restart_required``, nothing queued.
    2. The dispatcher issues an ordinary content reload (every program
       boundary does). A reload re-applies the program source only; the
       pipeline keeps the sinks it was BUILT with. Its settlement appends a
       health sample.
    3. The operator re-applies the same preset before the next poll tick.

    Before the fix, ``_commit_reload_settlement`` keyed that sample by the
    config row as it stood NOW (``pending.config``), so step 3 read "the
    running channel is already delivering them" / ``unchanged`` while the
    web preview had never been built -- for one automation tick, at every
    program boundary. Health must be keyed by ``_built_configs`` in EVERY
    appender, which is why the keying now lives in ``_sink_connected``.
    """
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    strategy = _SeamlessReloadStrategy()
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "work",
        source_plan_provider=lambda _channel_id: _program_plan(tmp_path),
        fallback_source_provider=lambda _config: _slate_plan(tmp_path),
        encoder_strategy=strategy,
    )
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime.now(UTC),
            issued_by="operator",
            command_id="cmd-start",
        )
    )
    assert daemon.process_once("gov") == 1
    state = store.read_state("gov")
    assert state is not None and state.state == "ON_AIR"
    assert [sink.kind for sink in strategy.starts[-1].config.sinks] == ["srt"]

    # Step 1: the operator enables the web preview while the program airs.
    payload = {"profile_id": "local-rehearsal-hls", "keep_existing_sinks": True}
    first = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["on_air_effect"] == "restart_required"
    assert store.pop_pending_commands("gov") == []

    # Step 2: a program-boundary content reload, armed on one tick and
    # settled on the next. The running pipeline still carries only ``srt``.
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="reload",
            issued_at=datetime.now(UTC),
            issued_by="automation",
            command_id="cmd-reload",
        )
    )
    assert daemon.process_once("gov") == 1
    assert len(strategy.reloads) == 1
    assert daemon.process_once("gov") == 0  # settlement commits; its health sample is newest
    assert len(strategy.starts) == 1, "a content reload never rebuilds the pipeline"

    # Step 3: re-apply inside the same tick -- the preview is NOT on air.
    second = client.post("/api/staff/egress/channels/gov/config/headend-profile", json=payload)

    assert second.status_code == 200, second.text
    assert second.json()["on_air_effect"] == "restart_required", second.json()
    assert "Stop and then Start" in second.json()["on_air_detail"]
    assert store.pop_pending_commands("gov") == []
    # And the settlement's own sample names only the sinks that were built.
    latest = store.recent_health("gov", 1)[0]
    assert set(latest.sink_connected) == {"Headend"}, latest.sink_connected


def test_restart_queued_tells_the_operator_every_output_drops_including_cable(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path, hls_root: Path
) -> None:
    # Review round 3 delta, MAJOR 1: the restart terminates the one worker that
    # produces every sink, cable headend feed included. The detail must say so.
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    _write_egress_state(store, "gov", "FALLBACK_SLATE")

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "local-rehearsal-hls", "keep_existing_sinks": True},
    )

    assert r.status_code == 200, r.text
    assert r.json()["on_air_effect"] == "restart_queued"
    detail = r.json()["on_air_detail"]
    assert "including the cable feed" in detail
    assert "drops" in detail
    assert "keep running" not in detail


@pytest.mark.parametrize(
    ("state", "effect", "phrase", "never"),
    [
        ("STOPPING", "next_start", "going off air", "not running"),
        ("DRAINING", "next_start", "going off air", "not running"),
        ("STOPPED", "next_start", "not running", "going off air"),
        ("ERROR", "next_start", "not running", "going off air"),
        ("STARTING", "restart_required", "starting up", "is on air"),
        ("ON_AIR", "restart_required", "is on air", "starting up"),
        ("TRANSITIONING", "restart_required", "is on air", "starting up"),
    ],
)
def test_apply_headend_profile_detail_sentence_matches_the_state(
    client: TestClient,
    store: PostgresEgressStore,
    state: str,
    effect: str,
    phrase: str,
    never: str,
) -> None:
    # Review round 3 delta, MINOR 4: STOPPING / DRAINING still have a worker
    # emitting ("not running" was false); STARTING is not on air yet.
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    _write_egress_state(store, "gov", state)

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "generic-udp-spts", "destination_uri": "udp://10.0.0.9:5000"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["on_air_effect"] == effect
    assert phrase in r.json()["on_air_detail"]
    assert never not in r.json()["on_air_detail"]
    assert store.pop_pending_commands("gov") == []


def test_slate_restart_is_one_durable_write(
    client: TestClient, store: PostgresEgressStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review round 3 delta, MINOR 1: stop + start were two committed writes; a
    # poll between them darkened the channel for a whole interval and a process
    # death between them left it STOPPED with no start queued. The route must
    # hand the pair to the store as ONE batch and never use the single-command
    # path for it.
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    _write_egress_state(store, "gov", "FALLBACK_SLATE")
    batches: list[list[str]] = []
    real_enqueue_commands = store.enqueue_commands

    def single(_cmd: EgressCommand) -> None:
        raise AssertionError("the restart pair must not be written one command at a time")

    def batch(cmds: list[EgressCommand]) -> None:
        batches.append([cmd.action for cmd in cmds])
        real_enqueue_commands(cmds)

    monkeypatch.setattr(store, "enqueue_command", single)
    monkeypatch.setattr(store, "enqueue_commands", batch)

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "generic-udp-spts", "destination_uri": "udp://10.0.0.9:5000"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["on_air_effect"] == "restart_queued"
    assert batches == [["stop", "start"]]
    assert [cmd.action for cmd in store.pop_pending_commands("gov")] == ["stop", "start"]


def test_slate_restart_never_cuts_a_program_that_committed_to_air_before_the_drain(
    client: TestClient, store: PostgresEgressStore, tmp_path: Path, hls_root: Path
) -> None:
    """Review round 3 delta, MINOR 2 -- the TOCTOU between the route's state
    read and the daemon's drain. The route read FALLBACK_SLATE and queued the
    restart; a program then committed to air on the running worker before the
    daemon drained the pair. Neither half may run: the stop would cut the
    program, and a start after a skipped stop is a stray."""
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    daemon, strategy = _daemon_on_slate(store, tmp_path)
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime.now(UTC),
            issued_by="operator",
            command_id="cmd-start",
        )
    )
    assert daemon.process_once("gov") == 1

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "local-rehearsal-hls", "keep_existing_sinks": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["on_air_effect"] == "restart_queued"
    # ... and in the window before the daemon polls, a program commits to air.
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            current_source_label="Council meeting",
            updated_at=datetime.now(UTC),
            pid=strategy.processes[0].pid,
        )
    )

    drained = daemon.process_once("gov")

    assert drained == 2  # both halves consumed ...
    assert strategy.processes[0].terminated is False  # ... but the program was not cut
    assert len(strategy.starts) == 1
    state = store.read_state("gov")
    assert state is not None
    assert state.state == "ON_AIR"
    assert state.current_source_label == "Council meeting"
    assert store.pop_pending_commands("gov") == []


def test_apply_headend_profile_rejected_destination_changes_nothing(
    client: TestClient, store: PostgresEgressStore
) -> None:
    # Validation runs BEFORE persistence and before any command: the stored
    # config and the state row are byte-identical afterwards.
    assert (
        client.put("/api/staff/egress/channels/gov/config", json=_config_payload()).status_code
        == 200
    )
    _write_egress_state(store, "gov", "FALLBACK_SLATE")
    config_before = store.get_config("gov")
    state_before = store.read_state("gov")

    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "comcast-mtd-sd", "destination_uri": "udp://10.0.0.5:5000"},
    )

    assert r.status_code == 422
    assert store.get_config("gov") == config_before
    assert store.read_state("gov") == state_before
    assert store.pop_pending_commands("gov") == []


# ---------------------------------------------------------------------------
# Round-2 delta review, BLOCKER 2: the preset was one of TWO operator-input
# write paths into the ``hls`` sink. ``PUT .../config`` ran only the model's
# shape check and accepted ``C:\Windows``, a UNC share, ``/etc`` and
# ``file:///C:/Windows`` -- each then served publicly by ``/media/live``.
# ---------------------------------------------------------------------------


def _hls_config_payload(uri: str) -> dict[str, object]:
    return _config_payload() | {"sinks": [{"kind": "hls", "label": "Web", "uri": uri}]}


def test_config_put_accepts_an_hls_sink_inside_the_root(client: TestClient, hls_root: Path) -> None:
    r = client.put(
        "/api/staff/egress/channels/gov/config", json=_hls_config_payload(str(hls_root / "gov"))
    )
    assert r.status_code == 200, r.text
    assert r.json()["sinks"][0]["kind"] == "hls"


@pytest.mark.parametrize(
    "uri",
    [
        "C:\\Windows",
        "\\\\fileserver\\share\\hls",
        "/etc",
        "file:///C:/Windows",
        "relative/folder",
    ],
)
def test_config_put_refuses_an_hls_sink_the_media_router_would_not_serve(
    client: TestClient, store: PostgresEgressStore, hls_root: Path, uri: str
) -> None:
    r = client.put("/api/staff/egress/channels/gov/config", json=_hls_config_payload(uri))

    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("hls sink 'Web': local HLS folder")
    assert store.get_config("gov") is None


def test_config_put_refuses_an_hls_sink_traversing_out_of_the_root(
    client: TestClient, store: PostgresEgressStore, hls_root: Path
) -> None:
    outside = str(hls_root / ".." / "elsewhere")
    r = client.put("/api/staff/egress/channels/gov/config", json=_hls_config_payload(outside))
    assert r.status_code == 422, r.text
    assert "outside the allowed root" in r.json()["detail"]
    assert store.get_config("gov") is None


def test_state_exposes_live_preparation_without_persisting_it(client, store, monkeypatch):
    monkeypatch.setattr("psutil.pid_exists", lambda _pid: True)
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="ON_AIR",
            pid=1234,
            current_source_label="Current program",
            updated_at=datetime.now(UTC),
        )
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            client.app.state,
            "egress_daemon",
            SimpleNamespace(
                source_preparation_status=lambda _channel: {
                    "preparation_status": "preparing",
                    "preparation_elapsed_seconds": 12.5,
                    "expected_handoff_at": datetime(2026, 10, 30, 12, tzinfo=UTC),
                }
            ),
            raising=False,
        )
        result = client.get("/api/staff/egress/channels/gov/state")
        assert result.status_code == 200
        assert result.json()["preparation_status"] == "preparing"
        assert result.json()["preparation_elapsed_seconds"] == 12.5
        assert result.json()["current_source_label"] == "Current program"
        assert store.read_state("gov").preparation_status is None


# --- Issue #324: a cable preset must never take the channel off air -----------------


def test_cable_preset_puts_ac3_on_the_cable_output_only(client: TestClient) -> None:
    """Every cable preset asks for AC-3 (SCTE 54 5.3: cable needs an AC-3 main audio
    service). Saved as the CHANNEL codec it took the channel off air on the default
    GStreamer engine and would have sent AC-3 to browsers. The apply puts AC-3 on the
    cable output alone (``-c:a ac3``) and keeps the channel, and so the web, on AAC."""
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "generic-udp-spts", "destination_uri": "udp://239.255.0.1:5000"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["config"]["canonical_profile"]["audio_codec"] == "aac"
    assert r.json()["config"]["canonical_profile"]["audio_bitrate_kbps"] == 192
    args = r.json()["config"]["sinks"][0]["extra_output_args"]
    assert args[args.index("-c:a") + 1] == "ac3"
    assert "The cable output carries AC-3 (Dolby Digital) sound" in r.json()["on_air_detail"]
    assert "cannot make" not in r.json()["on_air_detail"]
    persisted = client.get("/api/staff/egress/channels/gov/config").json()
    assert persisted["canonical_profile"]["audio_codec"] == "aac"
    assert "-c:a" in persisted["sinks"][0]["extra_output_args"]


def test_cable_preset_after_web_preview_leaves_the_web_output_on_aac(
    client: TestClient, hls_root: Path
) -> None:
    """The station case: web preview first, then a cable preset beside it."""
    web = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "local-rehearsal-hls", "destination_uri": ""},
    )
    assert web.status_code == 200, web.text
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={
            "profile_id": "comcast-mtd-hd",
            "destination_uri": "udp://239.255.0.1:5000",
            "keep_existing_sinks": True,
        },
    )

    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["canonical_profile"]["audio_codec"] == "aac"
    by_kind = {sink["kind"]: sink["extra_output_args"] for sink in config["sinks"]}
    assert "-c:a" not in by_kind["hls"]
    assert by_kind["udp-ts"][by_kind["udp-ts"].index("-c:a") + 1] == "ac3"


def test_cable_preset_keeps_ac3_on_the_ffmpeg_concat_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine-aware: the ffmpeg-concat engine can make AC-3, so it is not narrowed."""
    monkeypatch.setenv("CIVICCAST_EGRESS_ENGINE", "ffmpeg-concat")
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "generic-udp-spts", "destination_uri": "udp://239.255.0.1:5000"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["config"]["canonical_profile"]["audio_codec"] == "ac3"
    assert "-c:a" not in r.json()["config"]["sinks"][0]["extra_output_args"]
    assert "cable output carries" not in r.json()["on_air_detail"]
    assert "cannot make" not in r.json()["on_air_detail"]


def test_staff_egress_config_rejects_ac3_on_the_default_gstreamer_engine(
    client: TestClient,
) -> None:
    payload = _config_payload() | {"canonical_profile": {"audio_codec": "ac3"}}
    r = client.put("/api/staff/egress/channels/gov/config", json=payload)

    assert r.status_code == 422, r.text
    assert "ac3" in r.json()["detail"]
    assert "never goes on air" in r.json()["detail"]
    assert client.get("/api/staff/egress/channels/gov/config").status_code == 404


def test_staff_egress_config_allows_ac3_on_the_ffmpeg_concat_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_EGRESS_ENGINE", "ffmpeg-concat")
    payload = _config_payload() | {"canonical_profile": {"audio_codec": "ac3"}}
    r = client.put("/api/staff/egress/channels/gov/config", json=payload)

    assert r.status_code == 200, r.text
    assert r.json()["canonical_profile"]["audio_codec"] == "ac3"


def test_comcast_sd_preset_keeps_h264_on_the_default_gstreamer_engine(client: TestClient) -> None:
    """The Comcast SD preset also asks for MPEG-2 video. On the GStreamer engine a
    channel saved with it sent no video buffer and no new segment (R9, 2026-11-12)."""
    r = client.post(
        "/api/staff/egress/channels/gov/config/headend-profile",
        json={"profile_id": "comcast-mtd-sd", "destination_uri": "udp://239.255.0.1:5000"},
    )

    assert r.status_code == 200, r.text
    profile = r.json()["config"]["canonical_profile"]
    assert (profile["video_codec"], profile["audio_codec"]) == ("h264", "aac")
    assert "The cable output carries AC-3 (Dolby Digital) sound" in r.json()["on_air_detail"]
    assert "cannot make MPEG2VIDEO video yet" in r.json()["on_air_detail"]
    assert "uses H.264 video instead" in r.json()["on_air_detail"]


def test_staff_egress_config_rejects_mpeg2video_on_the_default_gstreamer_engine(
    client: TestClient,
) -> None:
    payload = _config_payload() | {"canonical_profile": {"video_codec": "mpeg2video"}}
    r = client.put("/api/staff/egress/channels/gov/config", json=payload)

    assert r.status_code == 422, r.text
    assert "video codec 'mpeg2video'" in r.json()["detail"]
