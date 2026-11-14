# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Policy: ``/health`` must not report "healthy" for a station that cannot work.

GauntletGate W-1 + T1 (Major, 2026-07-21). ``GET /health`` returned
``{"status":"healthy", ..., "schema":"not-configured"}`` on a station with no
database at all, and ``tests/test_schema_check.py`` asserted that pairing as the
expected contract -- so the suite locked the defect in. An uptime monitor or load
balancer polling ``/health`` saw a green station that could not serve a single
recording.

The contract these tests pin:

* ``/health`` is a LIVENESS probe: it answers **200 whenever the process is up**,
  in every schema state. The installer's Rust probe
  (``health_response_is_ok`` in ``src-tauri/src/main.rs``) requires 200, and a
  station mid-setup is alive-but-not-ready by design.
* ``status`` is the READINESS signal: ``healthy`` only when the database schema
  matches the code (``schema == "current"``); ``degraded`` otherwise -- whether
  the schema is missing, behind, or unverifiable.
* Readiness is re-evaluated when durable storage is activated mid-flight, so a
  station that just finished "Prepare storage" reports ``healthy`` WITHOUT a
  restart. Without this, ``degraded`` would be permanently sticky.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from civiccast.schema_check import expected_migration_head


def _sqlite_at_revision(tmp_path: Path, revision: str | None) -> str:
    """A sqlite file whose alembic_version holds ``revision`` (empty if None)."""

    db_path = tmp_path / "civiccast.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(255) NOT NULL)")
        if revision is not None:
            conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (revision,))
        conn.commit()
    return f"sqlite:///{db_path.as_posix()}"


@pytest.fixture
def health_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate the process-global state these tests necessarily disturb.

    Wiring durable storage binds a PROCESS-GLOBAL engine and writes
    ``DATABASE_URL``/``CIVICCAST_UPLOAD_DIR`` into ``os.environ`` directly (not
    through monkeypatch), so without this teardown every later test in the same
    session inherits an engine pointed at a deleted tmp_path sqlite file.
    """

    from civiccast.db import reset_engine

    monkeypatch.setenv("CIVICCAST_AUTH_ACK", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    expected_migration_head.cache_clear()
    try:
        yield
    finally:
        expected_migration_head.cache_clear()
        reset_engine()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("CIVICCAST_UPLOAD_DIR", None)


def _health(client: TestClient) -> dict[str, str]:
    response = client.get("/health")
    # Liveness never depends on readiness: the process is up, so it answers 200.
    assert response.status_code == 200, (
        "/health must stay 200 in every schema state -- the installer's Rust "
        "probe treats any non-200 as 'the service is not running' and will "
        "report a working install as failed."
    )
    return response.json()


def test_no_database_is_degraded_not_healthy(
    health_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    from civiccast.app import create_app

    # The schema check runs at LIFESPAN startup, so the client must enter it.
    with TestClient(create_app()) as client:
        body = _health(client)

    assert body["schema"] == "not-configured"
    assert body["status"] == "degraded", (
        "A station with no database cannot serve a recording. Reporting "
        "'healthy' tells an uptime monitor the opposite of the truth."
    )
    # Gate A run 33681670855: schema_db_revision/schema_expected_head are now
    # unconditional fields, not "behind"-only -- but check_schema_currency
    # never even calls expected_migration_head() on this path (no
    # database_url at all means an early return before either read), so both
    # render their "could not be determined" placeholders here.
    assert body["schema_db_revision"] == "none"
    assert body["schema_expected_head"] == "unknown"


def test_schema_at_head_is_healthy(
    health_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", _sqlite_at_revision(tmp_path, expected_migration_head()))
    from civiccast.app import create_app

    with TestClient(create_app()) as client:
        body = _health(client)

    assert body["schema"] == "current"
    assert body["status"] == "healthy"
    # Gate A run 33681670855: these two fields are unconditional now, so a
    # caller proving a post-upgrade migration actually landed can compare
    # them directly rather than trusting the "current" label alone.
    assert body["schema_db_revision"] == expected_migration_head()
    assert body["schema_expected_head"] == expected_migration_head()


def test_schema_behind_the_code_is_degraded(
    health_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # <installer-path-audit MA-06> "behind" now specifically means "a revision
    # THIS build's own migration graph recognizes, just not the head" -- a
    # revision the graph has never heard of is "ahead" instead (see
    # civiccast/schema_check.py's evaluate_schema_currency). The old fixture
    # value "0001_ancient_revision" was never a real revision id in this
    # repo's migration graph, so under that (correct, deliberate) distinction
    # it always classifies as "ahead", not "behind" -- this fixture predates
    # the ahead/behind split and was pinning an obsolete pairing. Use a real,
    # superseded revision id that IS in the graph so this test still exercises
    # "behind" rather than "ahead".
    ancient_revision = "0001_create_assets_table"
    monkeypatch.setenv("DATABASE_URL", _sqlite_at_revision(tmp_path, ancient_revision))
    from civiccast.app import create_app

    with TestClient(create_app()) as client:
        body = _health(client)

    assert body["schema"] == "behind"
    assert body["status"] == "degraded"
    # The drift detail that makes the state actionable must survive the change.
    assert body["schema_db_revision"] == ancient_revision
    assert body["schema_expected_head"] == expected_migration_head()


def test_unknown_schema_state_is_degraded_not_healthy(
    health_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unverifiable schema is not a healthy one -- fail honest, not green."""

    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    from civiccast.app import create_app
    from civiccast.schema_check import SchemaStatus

    app = create_app()
    with TestClient(app) as client:
        app.state.schema_status = SchemaStatus(state="unknown")
        body = _health(client)

    assert body["schema"] == "unknown"
    assert body["status"] == "degraded"


def test_readiness_recovers_when_storage_is_prepared_mid_flight(
    health_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ "Prepare storage" must flip /health to healthy without a restart.

    ``schema_status`` used to be computed once at lifespan startup and never
    again, so an operator who prepared storage from the console kept seeing
    ``not-configured`` until the service was bounced. With ``status`` derived
    from that value, the staleness would have turned into a permanently stuck
    ``degraded``.
    """

    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    from civiccast.app import create_app

    app = create_app()
    with TestClient(app) as client:
        assert _health(client)["status"] == "degraded"

        database_url = _sqlite_at_revision(tmp_path, expected_migration_head())
        app.state.activate_durable_storage(database_url)

        body = _health(client)

    assert body["schema"] == "current"
    assert body["status"] == "healthy"


def test_api_health_alias_reports_the_same_readiness(
    health_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    from civiccast.app import create_app

    with TestClient(create_app()) as client:
        alias = client.get("/api/health")

    assert alias.status_code == 200
    assert alias.json()["status"] == "degraded"


def test_caption_worker_failure_degrades_readiness_without_leaking_channel_details(
    health_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Liveness stays up while public readiness reflects on-air caption failure."""
    import json

    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    work_dir = tmp_path / "egress"
    status_path = work_dir / "government" / "captions" / "runtime-status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        json.dumps({"state": "storage-refused", "updated_at": "2026-10-09T12:00:00Z"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CIVICCAST_EGRESS_WORK_DIR", str(work_dir))
    monkeypatch.setattr(
        "civiccast.installer.station_state.resolve_live_captions_enabled_or_default",
        lambda: True,
    )

    from civiccast.app import create_app
    from civiccast.egress.models import EgressConfig, EgressSinkSpec, EgressStateRow
    from civiccast.egress.router import get_egress_store
    from civiccast.egress.store import InMemoryEgressStore
    from civiccast.schema_check import SchemaStatus

    store = InMemoryEgressStore()
    store.upsert_config(
        EgressConfig(
            channel_id="government",
            enabled=True,
            auto_start=False,
            slate_message="CivicCast is preparing the channel.",
            sinks=[
                EgressSinkSpec(kind="udp-ts", label="Cable headend", uri="udp://239.1.1.1:5000")
            ],
        )
    )
    store.write_state(
        EgressStateRow(
            channel_id="government",
            state="ON_AIR",
            updated_at=datetime.now(UTC),
        )
    )
    app = create_app()
    app.dependency_overrides[get_egress_store] = lambda: store

    with TestClient(app) as client:
        app.state.schema_status = SchemaStatus(state="current")
        body = _health(client)

    assert body["status"] == "degraded"
    assert body["live_captions"] == "degraded"
    assert "government" not in json.dumps(body)


def test_slate_channel_with_web_hls_down_degrades_readiness_without_leaking_details(
    health_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9 field bug (rc.5, 2026-11-12): a slate channel's frozen web HLS left
    ``/health`` "healthy" for a day because the probe never read egress."""
    import json

    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    monkeypatch.setattr(
        "civiccast.installer.station_state.resolve_live_captions_enabled_or_default",
        lambda: False,
    )

    from civiccast.app import create_app
    from civiccast.egress.models import (
        EgressConfig,
        EgressHealthSample,
        EgressSinkSpec,
        EgressStateRow,
    )
    from civiccast.egress.router import get_egress_store
    from civiccast.egress.store import InMemoryEgressStore
    from civiccast.schema_check import SchemaStatus

    store = InMemoryEgressStore()
    store.upsert_config(
        EgressConfig(
            channel_id="government",
            enabled=True,
            slate_message="CivicCast is preparing the channel.",
            sinks=[EgressSinkSpec(kind="hls", label="Web", uri="C:/CivicCast/live/government")],
        )
    )
    store.write_state(
        EgressStateRow(
            channel_id="government", state="FALLBACK_SLATE", updated_at=datetime.now(UTC)
        )
    )

    def _sample(web_ok: bool) -> EgressHealthSample:
        return EgressHealthSample(
            channel_id="government",
            sampled_at=datetime.now(UTC),
            state="FALLBACK_SLATE",
            sink_connected={"Web": web_ok},
        )

    app = create_app()
    app.dependency_overrides[get_egress_store] = lambda: store

    with TestClient(app) as client:
        app.state.schema_status = SchemaStatus(state="current")
        store.append_health(_sample(True))
        fresh = _health(client)
        store.append_health(_sample(False))
        frozen = _health(client)

    assert fresh["status"] == "healthy"
    assert fresh["slate_web_hls"] == "healthy"
    assert frozen["status"] == "degraded"
    assert frozen["slate_web_hls"] == "degraded"
    assert "government" not in json.dumps(frozen)
