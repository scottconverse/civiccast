# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Description admission, recovery and access boundaries on isolated media."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from civiccast.audio_description import router
from civiccast.audio_description.pipeline import (
    Checkpoints,
    Vision,
    choose_slot,
    extractive_shortening,
)
from civiccast.audio_description.service import DescriptionService, fingerprint, write_json
from civiccast.auth.models import OperatorIdentity


@pytest.fixture
def station(tmp_path, monkeypatch):
    source = tmp_path / "upload/meeting.mp4"
    source.parent.mkdir()
    source.write_bytes(b"original recording")
    asset = SimpleNamespace(
        asset_id="meeting-123",
        file_path=str(source),
        content_hash="source-v1",
        trim_in_seconds=0,
        trim_out_seconds=None,
        published_at="2026-10-31",
    )
    service = DescriptionService(
        tmp_path / "descriptions", tmp_path / "voice", "http://127.0.0.1:11439"
    )
    folder = service._folder(asset.asset_id)
    output = folder / "candidate/output"
    output.mkdir(parents=True)
    for name in (
        "original.mp4",
        "described.mp4",
        "captions.vtt",
        "descriptions.vtt",
        "narration-000.wav",
    ):
        (output / name).write_bytes(name.encode())
    manifest = {
        "outputs": {
            "original.mp4": "hash",
            "described.mp4": "hash",
            "captions.vtt": "hash",
            "descriptions.vtt": "hash",
        },
        "placed": [],
        "pause_descriptions": [
            {
                "id": 0,
                "time": 1,
                "until": 9,
                "text": "A map shows the park.",
                "audio": "narration-000.wav",
            }
        ],
        "coverage": {"in_gap": 0, "requires_pause": 1, "uncertain_visuals": 0},
        "limitations": ["Automatic descriptions can be incomplete."],
        "credits": ["Kyutai"],
    }
    write_json(output / "manifest.json", manifest)
    write_json(
        folder / "current.json", {"job_id": "candidate", "source_identity": fingerprint(asset)}
    )
    app = FastAPI()
    identity = {"role": "records_clerk"}
    policy = {"allow": True}

    @app.middleware("http")
    async def identify(request: Request, call_next):
        if identity["role"]:
            request.state.operator_identity = OperatorIdentity(
                operator_id="local-test", operator_display_name="Test", scopes=(identity["role"],)
            )
        return await call_next(request)

    app.include_router(router.staff_router)
    app.include_router(router.preview_router)
    app.include_router(router.public_router)
    app.dependency_overrides[router.get_service] = lambda: service
    app.dependency_overrides[router.get_asset_store] = lambda: SimpleNamespace(
        get_staff_row=lambda _: asset
    )
    app.dependency_overrides[router.get_paywall_service] = lambda: SimpleNamespace(
        viewer_email=lambda *_: None, has_access=lambda *_: policy["allow"]
    )
    app.dependency_overrides[router.get_recording_target_store] = lambda: None
    app.dependency_overrides[router.get_ai_model_service] = lambda: SimpleNamespace(
        effective_model_tag=lambda _: "gemma4:12b"
    )
    monkeypatch.setenv("CIVICCAST_UPLOAD_DIR", str(source.parent))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield SimpleNamespace(
            client=client,
            service=service,
            source=source,
            asset=asset,
            output=output,
            identity=identity,
            policy=policy,
            app=app,
        )
    service.close()


def test_private_preview_requires_asset_scoped_expiring_grant(station):
    path = "/media/staff-audio-description/meeting-123"
    assert station.client.get(path + "/preview").status_code == 401
    response = station.client.post(
        "/api/staff/assets/meeting-123/audio-description/preview-session"
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert station.client.get(response.json()["url"]).status_code == 200
    assert station.client.get(path + "/narration-000.wav").status_code == 200
    assert station.client.get(path + "/worker.log").status_code == 404
    assert (
        station.client.get("/media/staff-audio-description/another-asset/preview").status_code
        == 401
    )
    station.service._grants.clear()
    assert station.client.get(path + "/preview").status_code == 401


def test_public_description_rechecks_publication_access_and_source(station):
    path = "/api/public/assets/meeting-123/audio-description"
    response = station.client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert "source_sha256" not in response.json()
    assert station.client.get(path + "/described.mp4").status_code == 200
    station.policy["allow"] = False
    assert station.client.get(path + "/described.mp4").status_code == 403
    station.policy["allow"] = True
    station.asset.published_at = None
    assert station.client.get(path).status_code == 404
    station.asset.published_at = "2026-10-31"
    station.source.write_bytes(b"replaced source")
    assert station.client.get(path).status_code == 404


def test_staff_role_and_caption_preflight(station):
    path = "/api/staff/assets/meeting-123/audio-description"
    station.identity["role"] = "publish_operator"
    assert station.client.post(path + "/generate").status_code == 403
    station.identity["role"] = ""
    assert station.client.get(path).status_code == 401
    station.identity["role"] = "records_clerk"
    assert station.client.post(path + "/generate").status_code == 422
    assert station.source.read_bytes() == b"original recording"


def test_trim_change_requires_repackaging(station, monkeypatch, tmp_path):
    package = tmp_path / "package"
    captions = package / "captions/captions.vtt"
    captions.parent.mkdir(parents=True)
    captions.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nWords\n", encoding="utf-8")
    write_json(
        package / "playback-timeline.json",
        {"version": 1, "source_start_seconds": 0, "source_end_seconds": None},
    )
    monkeypatch.setattr(router, "resolve_vod_package_dir", lambda _: package)
    station.asset.trim_in_seconds = 3
    assert (
        station.client.post("/api/staff/assets/meeting-123/audio-description/generate").status_code
        == 409
    )


def test_restart_reports_interrupted_and_preserves_original(station):
    folder = station.service._folder(station.asset.asset_id)
    write_json(
        folder / "job.json",
        {"asset_id": station.asset.asset_id, "job_id": "unfinished", "state": "running"},
    )
    assert station.service.status(station.asset.asset_id)["state"] == "interrupted"
    assert station.service.current(station.asset) is not None
    station.asset.trim_out_seconds = 8
    assert station.service.current(station.asset) is None


def test_storage_has_one_owner_and_preview_cannot_leave_root(tmp_path):
    one = DescriptionService(tmp_path / "shared", tmp_path, "http://127.0.0.1:11434")
    two = DescriptionService(tmp_path / "shared", tmp_path, "http://127.0.0.1:11434")
    try:
        one._claim()
        with pytest.raises(ValueError, match="Another CivicCast process"):
            two._claim()
        with pytest.raises(ValueError):
            one._folder("../outside")
    finally:
        one.close()
        two.close()


def test_resume_checkpoints_verify_outputs_and_source(tmp_path):
    checkpoints = Checkpoints(tmp_path, {"source": "one"})
    artifact = tmp_path / "narration.wav"

    def generate():
        artifact.write_bytes(b"voice")
        return {"text": "A park"}, [artifact]

    checkpoints.stage("voice", generate)
    assert checkpoints.stage("voice", lambda: pytest.fail("must reuse verified artifact")) == {
        "text": "A park"
    }
    artifact.write_bytes(b"corrupted")
    checkpoints.stage("voice", generate)
    assert artifact.read_bytes() == b"voice"
    with pytest.raises(ValueError, match="changed"):
        Checkpoints(tmp_path, {"source": "two"})
    (tmp_path / "CANCEL").touch()
    with pytest.raises(InterruptedError):
        checkpoints.stage("voice", generate)


def test_fit_preserves_speech_and_extractively_shortens():
    event = {"time": 1, "until": 15}
    assert choose_slot(event, 3, [(2, 12)], [(2, 5)]) == 5
    assert choose_slot(event, 12, [(2, 12)], []) is None
    assert (
        extractive_shortening("The budget is $2.5 million.", "budget is $2.5 million.")
        == "budget is $2.5 million."
    )
    assert extractive_shortening("The budget is $2.5 million.", "budget is $2 million.") is None


@pytest.mark.parametrize(
    "endpoint", ["https://127.0.0.1:11434", "http://example.com", "http://127.0.0.1:11434/extra"]
)
def test_nonlocal_model_endpoint_is_rejected(tmp_path, endpoint):
    with pytest.raises(ValueError, match="loopback"):
        Vision("gemma4:12b", endpoint, tmp_path)


def test_live_wait_is_cancellable(tmp_path):
    from civiccast.audio_description.pipeline import await_background

    (tmp_path / "WAIT").touch()
    (tmp_path / "CANCEL").touch()
    with pytest.raises(InterruptedError, match="waiting for live captions"):
        await_background(tmp_path)


def test_model_selection_migration_preserves_existing_selections(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{tmp_path / 'model-selection.sqlite'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0098_channel_repeat_schedule")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO feature_model_registry (registry_id, feature, model_key, created_at, updated_at) VALUES ('existing-summary','summary','gemma4-e4b-ollama',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
        command.upgrade(config, "0099_audio_description_model")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT model_key FROM feature_model_registry WHERE registry_id='existing-summary'"
                    )
                ).scalar_one()
                == "gemma4-e4b-ollama"
            )
            connection.execute(
                text(
                    "INSERT INTO feature_model_registry (registry_id, feature, model_key, created_at, updated_at) VALUES ('new-description','audio_description','gemma4-12b-ollama',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
        command.downgrade(config, "0098_channel_repeat_schedule")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT feature FROM feature_model_registry")
            ).scalars().all() == ["summary"]
    finally:
        engine.dispose()
