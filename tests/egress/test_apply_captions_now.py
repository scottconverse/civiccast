# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""rc.6.1 item 3: the live-caption switch only takes effect at a channel start.

Three parts, three sets of tests:

1. The station profile save tells the operator this when it changes the
   switch (``live_captions_restart_required`` + ``live_captions_notice``).
2. ``POST /api/staff/egress/channels/{id}/apply-captions`` queues a prefixed
   ``reload`` for that ONE channel, and refuses (409) when it cannot apply.
3. The daemon turns that reload into a restart of that one channel's worker,
   which rebuilds the caption legs. It never restarts the service.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

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
from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.encoder_strategy import EncoderStartRequest, EncoderStartResult
from civiccast.egress.models import (
    APPLY_CAPTIONS_COMMAND_PREFIX,
    CanonicalProfile,
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressSourcePlan,
    EgressSourceSegment,
    EgressStateRow,
)
from civiccast.egress.router import get_egress_store
from civiccast.egress.store import InMemoryEgressStore, PostgresEgressStore
from civiccast.installer.models import FirstAdminSetupRequest
from civiccast.installer.service import complete_first_admin_setup
from civiccast.live.router import get_live_session_store

_OPERATOR_HEADERS = {"Authorization": "Bearer operator-token-a"}


# --- 1. the profile save tells the operator ---------------------------------


@pytest.fixture
def profile_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("CIVICCAST_STATION_STATE_PATH", str(tmp_path / "station-state.json"))
    monkeypatch.delenv("CIVICCAST_CAPTION_TAP", raising=False)
    complete_first_admin_setup(
        FirstAdminSetupRequest(
            station_name="Pinegrove School Board",
            admin_display_name="Avery Admin",
            admin_username="avery",
            admin_password="correct horse battery staple",
            recovery_kit_destination="safe",
        )
    )
    return TestClient(create_app(), headers=_OPERATOR_HEADERS)


def _put_profile(client: TestClient, payload: dict[str, object]) -> dict[str, object]:
    response = client.put("/api/staff/station/profile", json=payload)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


@pytest.mark.parametrize("target", [True, False])
def test_changing_the_switch_says_it_applies_at_the_next_start(
    profile_client: TestClient, target: bool
) -> None:
    # Start from the opposite value, whatever the shipped default is.
    _put_profile(profile_client, {"live_captions_enabled": not target})

    body = _put_profile(profile_client, {"live_captions_enabled": target})

    assert body["live_captions_enabled"] is target
    assert body["live_captions_restart_required"] is True
    notice = str(body["live_captions_notice"])
    assert f"now {'on' if target else 'off'}" in notice
    assert "next start" in notice
    assert "Apply captions now" in notice


def test_saving_the_same_value_carries_no_notice(profile_client: TestClient) -> None:
    _put_profile(profile_client, {"live_captions_enabled": True})

    body = _put_profile(profile_client, {"live_captions_enabled": True})

    assert body["live_captions_restart_required"] is False
    assert body["live_captions_notice"] is None


def test_editing_another_field_carries_no_notice(profile_client: TestClient) -> None:
    body = _put_profile(profile_client, {"station_name": "Pinegrove Board of Education"})

    assert body["live_captions_restart_required"] is False
    assert body["live_captions_notice"] is None


def test_environment_off_says_captions_stay_off(
    profile_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")

    body = _put_profile(profile_client, {"live_captions_enabled": True})

    # Nothing to restart for: the environment keeps every channel off.
    assert body["live_captions_restart_required"] is False
    assert "CIVICCAST_CAPTION_TAP=off" in str(body["live_captions_notice"])


def test_a_profile_read_carries_no_notice_fields(profile_client: TestClient) -> None:
    body = profile_client.get("/api/staff/station/profile").json()

    assert "live_captions_notice" not in body
    assert "live_captions_restart_required" not in body


# --- 2. the per-channel endpoint ---------------------------------------------


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
    app.dependency_overrides[get_live_session_store] = lambda: SimpleNamespace(
        list_sessions=lambda **_kwargs: []
    )
    with TestClient(app, headers=_OPERATOR_HEADERS) as c:
        yield c


def _config(channel_id: str = "gov", *, enabled: bool = True) -> EgressConfig:
    return EgressConfig(
        channel_id=channel_id,
        enabled=enabled,
        slate_message="Stand by.",
        canonical_profile=CanonicalProfile(),
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri=f"build/{channel_id}.ts")],
    )


def _set_state(store: PostgresEgressStore | InMemoryEgressStore, channel: str, state: str) -> None:
    store.write_state(
        EgressStateRow(channel_id=channel, state=state, updated_at=datetime.now(UTC))  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("state", ["ON_AIR", "FALLBACK_SLATE"])
def test_apply_captions_queues_one_prefixed_reload_for_that_channel_only(
    client: TestClient, store: PostgresEgressStore, state: str
) -> None:
    store.upsert_config(_config("gov"))
    store.upsert_config(_config("edu"))
    _set_state(store, "gov", state)
    _set_state(store, "edu", "ON_AIR")

    response = client.post("/api/staff/egress/channels/gov/apply-captions")

    assert response.status_code == 202, response.text
    queued = store.pop_pending_commands("gov")
    assert [c.action for c in queued] == ["reload"]
    assert queued[0].command_id.startswith(APPLY_CAPTIONS_COMMAND_PREFIX)
    assert response.json()["command"]["command_id"] == queued[0].command_id
    # The other channel is not touched.
    assert store.pop_pending_commands("edu") == []


@pytest.mark.parametrize("state", [None, "STOPPED", "STARTING", "TRANSITIONING", "DRAINING"])
def test_apply_captions_is_refused_when_the_channel_is_not_steady_on_air(
    client: TestClient, store: PostgresEgressStore, state: str | None
) -> None:
    store.upsert_config(_config("gov"))
    if state is not None:
        _set_state(store, "gov", state)

    response = client.post("/api/staff/egress/channels/gov/apply-captions")

    assert response.status_code == 409
    assert "next starts" in response.json()["detail"]
    assert store.pop_pending_commands("gov") == []


@pytest.mark.parametrize("configured", [False, True])
def test_apply_captions_is_refused_without_an_enabled_config(
    client: TestClient, store: PostgresEgressStore, configured: bool
) -> None:
    if configured:
        store.upsert_config(_config("gov", enabled=False))
    _set_state(store, "gov", "ON_AIR")

    response = client.post("/api/staff/egress/channels/gov/apply-captions")

    assert response.status_code == 409
    assert "not configured and enabled" in response.json()["detail"]
    assert store.pop_pending_commands("gov") == []


def test_apply_captions_needs_an_operator_role(
    client: TestClient, store: PostgresEgressStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "CIVICCAST_STAFF_TOKENS", "records-token-c:records-c:Records C:records_clerk"
    )
    store.upsert_config(_config("gov"))
    _set_state(store, "gov", "ON_AIR")

    response = client.post(
        "/api/staff/egress/channels/gov/apply-captions",
        headers={"Authorization": "Bearer records-token-c"},
    )

    assert response.status_code == 403
    assert store.pop_pending_commands("gov") == []


# --- 3. the daemon restarts that one channel's worker -----------------------


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _Strategy:
    name = "fake-content-reload"
    supports_live_swap = False
    supports_content_reload = True
    supports_scheduled_start = True

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.processes: list[_FakeProcess] = []
        self.start_requests: list[EncoderStartRequest] = []
        self.reload_requests: list[EncoderStartRequest] = []

    def start(self, request: EncoderStartRequest) -> EncoderStartResult:
        self.start_requests.append(request)
        process = _FakeProcess(4200 + len(self.processes))
        self.processes.append(process)
        return EncoderStartResult(
            process=process,
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
        return True


def _plan(tmp_path: Path, label: str) -> EgressSourcePlan:
    source = tmp_path / f"{label.lower().replace(' ', '-')}.ts"
    source.write_text(label, encoding="utf-8")
    return EgressSourcePlan(
        channel_id="public",
        segments=[
            EgressSourceSegment(
                label=label, path=str(source), duration_seconds=300, source_ref=f"ref-{label}"
            )
        ],
    )


def _daemon(
    tmp_path: Path, *, enabled: bool = True
) -> tuple[EgressDaemon, InMemoryEgressStore, _Strategy]:
    store = InMemoryEgressStore()
    store.upsert_config(_config("public", enabled=enabled))
    strategy = _Strategy(tmp_path)
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path / "egress",
        source_plan_provider=lambda _cid: _plan(tmp_path, "Program A"),
        encoder_strategy=strategy,
    )
    return daemon, store, strategy


def _apply_command() -> EgressCommand:
    return EgressCommand(
        channel_id="public",
        action="reload",
        issued_at=datetime.now(UTC),
        issued_by="operator-a",
        command_id=f"{APPLY_CAPTIONS_COMMAND_PREFIX}test",
    )


@pytest.mark.parametrize("state", ["ON_AIR", "FALLBACK_SLATE"])
def test_apply_captions_restarts_the_running_worker_and_starts_it_again(
    tmp_path: Path, state: str
) -> None:
    daemon, store, strategy = _daemon(tmp_path)
    daemon._start("public")
    _set_state(store, "public", state)
    assert len(strategy.start_requests) == 1

    store.enqueue_command(_apply_command())
    daemon.process_once("public")

    # The worker is terminated now, not after the program ends, and no
    # in-place content reload is tried (it would not rebuild the caption legs).
    assert strategy.processes[0].terminated
    assert strategy.reload_requests == []

    for _ in range(5):
        daemon.process_once("public")
        if len(strategy.start_requests) == 2:
            break
    # The restart started a NEW worker for the same channel with the plan.
    assert len(strategy.start_requests) == 2
    assert strategy.start_requests[1].source_plan.segments[0].label == "Program A"
    assert not strategy.processes[1].terminated


@pytest.mark.parametrize("state", ["STARTING", "TRANSITIONING", "DRAINING", "STOPPED"])
def test_apply_captions_leaves_a_channel_that_is_not_steady_alone(
    tmp_path: Path, state: str
) -> None:
    daemon, store, strategy = _daemon(tmp_path)
    daemon._start("public")
    _set_state(store, "public", state)

    store.enqueue_command(_apply_command())
    daemon.process_commands_only("public")

    assert not strategy.processes[0].terminated
    assert len(strategy.start_requests) == 1


def test_apply_captions_does_not_start_a_channel_that_is_not_running(tmp_path: Path) -> None:
    daemon, store, strategy = _daemon(tmp_path)
    _set_state(store, "public", "ON_AIR")

    store.enqueue_command(_apply_command())
    daemon.process_once("public")

    assert strategy.start_requests == []


def test_apply_captions_ignores_a_disabled_channel(tmp_path: Path) -> None:
    daemon, store, strategy = _daemon(tmp_path)
    daemon._start("public")
    _set_state(store, "public", "ON_AIR")
    store.upsert_config(_config("public", enabled=False))

    store.enqueue_command(_apply_command())
    daemon.process_once("public")

    assert not strategy.processes[0].terminated


def test_a_plain_reload_is_unchanged(tmp_path: Path) -> None:
    daemon, store, strategy = _daemon(tmp_path)
    daemon._start("public")
    _set_state(store, "public", "ON_AIR")

    store.enqueue_command(
        EgressCommand(
            channel_id="public",
            action="reload",
            issued_at=datetime.now(UTC),
            issued_by="operator-a",
            command_id="egress-plain-reload",
        )
    )
    daemon.process_once("public")

    # The ordinary reload still tries the seamless in-place content reload.
    assert len(strategy.reload_requests) == 1
    assert not strategy.processes[0].terminated
