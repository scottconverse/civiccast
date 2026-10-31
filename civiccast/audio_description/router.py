# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Asset-bound staff jobs and access-controlled resident derivatives."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from civiccast.ai_models.router import get_ai_model_service
from civiccast.audio_description.models import (
    AudioDescriptionJob,
    DescriptionPlayback,
    DescriptionPreviewSession,
)
from civiccast.audio_description.service import DescriptionService
from civiccast.auth.roles import require_any_role
from civiccast.captions.vod import package_source_range, published_caption_sidecar
from civiccast.egress.router import get_egress_store
from civiccast.live.router import get_live_session_store, get_recording_target_store
from civiccast.paywall.router import _station_id, get_paywall_service
from civiccast.schedule.paths import resolve_upload_root, resolve_vod_package_dir
from civiccast.schedule.router import (
    _allowed_source_path,
    _configured_source_roots,
    get_asset_store,
)

_ROLES = ("records_clerk", "meeting_operator", "support_admin", "setup_admin")
_INIT = threading.Lock()
staff_router = APIRouter(
    prefix="/api/staff/assets/{asset_id}/audio-description",
    tags=["audio-description"],
    dependencies=[Depends(require_any_role(*_ROLES))],
)
public_router = APIRouter(prefix="/api/public/assets/{asset_id}/audio-description", tags=["public"])
preview_router = APIRouter(
    prefix="/media/staff-audio-description/{asset_id}", tags=["staff-preview"]
)
_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Vary": "Cookie",
}


def get_service(
    request: Request,
    sessions: Any = Depends(get_live_session_store),
    egress: Any = Depends(get_egress_store),
) -> DescriptionService:
    with _INIT:
        service = getattr(request.app.state, "audio_description_service", None)
        if service is None:
            upload = resolve_upload_root()
            if upload is None:
                raise HTTPException(
                    503, "Configure the station upload folder before generating audio descriptions."
                )
            voice = os.environ.get("CIVICCAST_AD_VOICE_DIR", "").strip()
            if not voice:
                raise HTTPException(
                    503,
                    "Provision the local audio-description narrator and configure CIVICCAST_AD_VOICE_DIR.",
                )
            root = Path(
                os.environ.get("CIVICCAST_AD_DATA_DIR") or upload / ".civiccast-audio-description"
            )
            endpoint = os.environ.get("CIVICCAST_AD_OLLAMA_URL", "http://127.0.0.1:11434")

            def live_busy() -> bool:
                if sessions is not None and sessions.list_sessions(states=("on_air",)):
                    return True
                if egress is not None:
                    for config in egress.list_configs():
                        state = egress.read_state(config.channel_id)
                        if state is not None and state.state in {
                            "STARTING",
                            "ON_AIR",
                            "TRANSITIONING",
                        }:
                            return True
                return False

            service = DescriptionService(root, Path(voice), endpoint, live_busy)
            request.app.state.audio_description_service = service
        return service


def staff_asset(asset_id: str, store: Any) -> Any:
    if not hasattr(store, "get_staff_row"):
        raise HTTPException(503, "Recording storage is not ready.")
    asset = store.get_staff_row(asset_id)
    if asset is None:
        raise HTTPException(404, "Recording not found.")
    return asset


@staff_router.get("", response_model=AudioDescriptionJob)
def status(
    asset_id: str,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
) -> dict[str, Any]:
    staff_asset(asset_id, store)
    return service.status(asset_id)


@staff_router.post("/generate", status_code=202, response_model=AudioDescriptionJob)
@staff_router.post("/resume", status_code=202, response_model=AudioDescriptionJob)
def generate(
    asset_id: str,
    request: Request,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
    targets: Any = Depends(get_recording_target_store),
    models: Any = Depends(get_ai_model_service),
) -> dict[str, Any]:
    asset = staff_asset(asset_id, store)
    source = _allowed_source_path(asset.file_path, _configured_source_roots(targets))
    package = resolve_vod_package_dir(asset_id)
    captions = published_caption_sidecar(package) if package else None
    if source is None:
        raise HTTPException(
            422, "The recording source is unavailable or outside configured media folders."
        )
    if package is None or captions is None or not captions.is_file():
        raise HTTPException(
            422,
            "This recording needs its English caption track before audio-description generation.",
        )
    packaged_start, packaged_end = package_source_range(package)
    if (
        abs(packaged_start - (asset.trim_in_seconds or 0)) > 0.05
        or packaged_end != asset.trim_out_seconds
    ):
        raise HTTPException(
            409,
            "Repackage the recording after changing its trim before generating audio descriptions.",
        )
    if models is None:
        raise HTTPException(503, "The station model configuration is unavailable.")
    try:
        return service.submit(
            asset,
            source,
            captions,
            models.effective_model_tag("audio_description"),
            resume=request.url.path.endswith("/resume"),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            503,
            "The selected local vision model is unavailable. Check Settings > AI Models > Audio description.",
        ) from exc


@staff_router.post("/cancel", response_model=AudioDescriptionJob)
def cancel(
    asset_id: str,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
) -> dict[str, Any]:
    staff_asset(asset_id, store)
    try:
        return service.cancel(asset_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _current(asset: Any, service: DescriptionService) -> tuple[Path, dict[str, Any]]:
    try:
        result = service.current(asset)
    except OSError:
        result = None
    if result is None:
        raise HTTPException(404, "No current audio description is available for this recording.")
    return result


def _manifest(manifest: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "coverage": manifest["coverage"],
        "limitations": manifest["limitations"],
        "credits": manifest["credits"],
        "original_url": prefix + "/original.mp4",
        "described_url": prefix + "/described.mp4",
        "captions_url": prefix + "/captions.vtt",
        "descriptions_url": prefix + "/descriptions.vtt",
        "pause_descriptions": [
            {
                "id": x["id"],
                "time": x["time"],
                "until": x["until"],
                "text": x["text"],
                "audio": prefix + "/" + x["audio"],
            }
            for x in manifest["pause_descriptions"]
        ],
    }


def _file(root: Path, manifest: dict[str, Any], filename: str) -> FileResponse:
    allowed = set(manifest["outputs"]) - {"manifest.json"}
    allowed.update(x["audio"] for key in ("placed", "pause_descriptions") for x in manifest[key])
    path = (root / filename).resolve()
    if filename not in allowed or not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Description media not found.")
    return FileResponse(path, headers=_HEADERS)


@staff_router.post("/preview-session", response_model=DescriptionPreviewSession)
def preview_session(
    asset_id: str,
    response: Response,
    request: Request,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
) -> dict[str, str]:
    _current(staff_asset(asset_id, store), service)
    path = f"/media/staff-audio-description/{asset_id}"
    response.set_cookie(
        "cc_ad_" + asset_id,
        service.preview_grant(asset_id),
        max_age=900,
        path=path,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https"
        or request.url.hostname not in {"127.0.0.1", "localhost", "::1"},
    )
    response.headers["Cache-Control"] = "no-store"
    return {"url": path + "/preview"}


@preview_router.get("/{filename}", include_in_schema=False)
def preview(
    asset_id: str,
    filename: str,
    request: Request,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
) -> Any:
    if not service.preview_allowed(asset_id, request.cookies.get("cc_ad_" + asset_id, "")):
        raise HTTPException(
            401,
            "Open the recording preview from the operator console; preview access expires after 15 minutes.",
        )
    root, manifest = _current(staff_asset(asset_id, store), service)
    if filename == "manifest.json":
        return manifest
    if filename != "preview":
        return _file(root, manifest, filename)
    html = (Path(__file__).parent / "preview.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html,
        headers={
            **_HEADERS,
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; media-src 'self'; frame-ancestors 'self'",
        },
    )


@staff_router.get("/{filename}", include_in_schema=False)
def staff_file(
    asset_id: str,
    filename: str,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
) -> Any:
    root, manifest = _current(staff_asset(asset_id, store), service)
    if filename == "manifest.json":
        return manifest
    return _file(root, manifest, filename)


def public_asset(asset_id: str, request: Request, store: Any, paywall: Any) -> Any:
    asset = staff_asset(asset_id, store)
    if asset.published_at is None:
        raise HTTPException(404, "Recording not found.")
    if paywall is None:
        raise HTTPException(503, "Playback access policy is unavailable.")
    email = paywall.viewer_email(_station_id(), request.cookies.get("civiccast_viewer"))
    if not paywall.has_access(_station_id(), email, "asset", asset_id):
        raise HTTPException(403, "Sign in through the station portal to play this recording.")
    return asset


@public_router.get("", response_model=DescriptionPlayback)
def public_manifest(
    asset_id: str,
    request: Request,
    response: Response,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
    paywall: Any = Depends(get_paywall_service),
) -> dict[str, Any]:
    asset = public_asset(asset_id, request, store, paywall)
    _, manifest = _current(asset, service)
    response.headers.update(_HEADERS)
    return _manifest(manifest, f"/api/public/assets/{asset_id}/audio-description")


@public_router.get("/{filename}", include_in_schema=False)
def public_file(
    asset_id: str,
    filename: str,
    request: Request,
    service: DescriptionService = Depends(get_service),
    store: Any = Depends(get_asset_store),
    paywall: Any = Depends(get_paywall_service),
) -> FileResponse:
    asset = public_asset(asset_id, request, store, paywall)
    return _file(*_current(asset, service), filename)
