# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Durable, serial audio-description jobs. Originals are never modified."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import portalocker


def now() -> str:
    return datetime.now(UTC).isoformat()


def fingerprint(asset: Any) -> str:
    source = Path(asset.file_path).resolve()
    stat = source.stat()
    identity = [
        str(source),
        stat.st_size,
        stat.st_mtime_ns,
        asset.content_hash,
        asset.trim_in_seconds,
        asset.trim_out_seconds,
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


class DescriptionService:
    """One owning process and one admitted job, with explicit interrupted resume."""

    def __init__(
        self, root: Path, voice: Path, endpoint: str, live_busy: Callable[[], bool] = lambda: False
    ) -> None:
        self.root, self.voice, self.endpoint = root.resolve(), voice.resolve(), endpoint
        self._live_busy = live_busy
        self._mutex = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="civiccast-ad")
        self._active: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._owner: Any = None
        self._grants: dict[str, tuple[str, float]] = {}

    def preview_grant(self, asset_id: str) -> str:
        with self._mutex:
            self._grants = {
                key: value for key, value in self._grants.items() if value[1] > time.time()
            }
            if len(self._grants) >= 256:
                self._grants.pop(next(iter(self._grants)))
            token = secrets.token_urlsafe(32)
            self._grants[hashlib.sha256(token.encode()).hexdigest()] = (asset_id, time.time() + 900)
            return token

    def preview_allowed(self, asset_id: str, token: str) -> bool:
        if len(token) > 200:
            return False
        with self._mutex:
            value = self._grants.get(hashlib.sha256(token.encode()).hexdigest())
            return bool(value and value[0] == asset_id and value[1] > time.time())

    def preflight(self) -> None:
        for tool in ("ffmpeg", "ffprobe"):
            if shutil.which(tool) is None:
                raise ValueError(f"{tool} is missing. Repair the station media tools.")
        if any(
            importlib.util.find_spec(name) is None for name in ("pocket_tts", "webvtt", "webrtcvad")
        ):
            raise ValueError(
                "Audio-description dependencies are missing. Install civiccast[audio-description]."
            )
        for name in ("local.yaml", "model.safetensors", "tokenizer.json", "alba.safetensors"):
            if not (self.voice / name).is_file():
                raise ValueError(
                    "The local narrator is not provisioned. Run the documented audio-description provisioning step."
                )

    def _claim(self) -> None:
        if self._owner is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        owner = (self.root / ".owner.lock").open("a+b")
        try:
            portalocker.lock(owner, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except (OSError, portalocker.LockException) as exc:
            owner.close()
            raise ValueError(
                "Another CivicCast process owns audio-description storage. Use one service owner."
            ) from exc
        self._owner = owner

    def _folder(self, asset_id: str) -> Path:
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", asset_id):
            raise ValueError("Invalid recording identity")
        folder = (self.root / asset_id).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError("Recording folder leaves audio-description storage")
        return folder

    def status(self, asset_id: str) -> dict[str, Any]:
        with self._mutex:
            path = self._folder(asset_id) / "job.json"
            if not path.is_file():
                return {"state": "not_generated", "asset_id": asset_id}
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["state"] in ("queued", "running") and self._active != asset_id:
                job = {
                    **job,
                    "state": "interrupted",
                    "message": "Generation was interrupted. Resume to reuse verified checkpoints.",
                }
            return cast(dict[str, Any], job)

    def submit(
        self, asset: Any, source: Path, captions: Path, model: str, *, resume: bool = False
    ) -> dict[str, Any]:
        self.preflight()
        from civiccast.audio_description.pipeline import Vision

        Vision(model, self.endpoint, self.root / "provider-traces")
        with self._mutex:
            if self._closed:
                raise ValueError("Audio-description service is shutting down. Retry after restart.")
            self._claim()
            if self._active:
                raise ValueError(
                    "A recording is already being described. Wait for it or cancel it first."
                )
            identity = fingerprint(asset)
            folder = self._folder(asset.asset_id)
            folder.mkdir(exist_ok=True)
            prior = self.status(asset.asset_id)
            if resume:
                if prior["state"] not in ("interrupted", "failed", "cancelled"):
                    raise ValueError("This recording has no interrupted job to resume.")
                if prior["source_identity"] != identity or prior["model"] != model:
                    raise ValueError(
                        "The source or selected model changed. Generate a new description instead."
                    )
                job = prior
            else:
                job = {
                    "asset_id": asset.asset_id,
                    "job_id": uuid4().hex,
                    "created_at": now(),
                    "source_identity": identity,
                    "model": model,
                    "trim_in": asset.trim_in_seconds or 0,
                    "trim_out": asset.trim_out_seconds,
                }
            work = folder / job["job_id"]
            work.mkdir(exist_ok=True)
            (work / "CANCEL").unlink(missing_ok=True)
            (work / "output/CANCEL").unlink(missing_ok=True)
            # Copy captions now; a later caption job cannot change this job in place.
            if not resume:
                shutil.copyfile(captions, work / "input.vtt")
            request = {
                "source": str(source),
                "output": str(work / "output"),
                "captions": str(work / "input.vtt"),
                "model": model,
                "endpoint": self.endpoint,
                "trim_in": job["trim_in"],
                "trim_out": job["trim_out"],
            }
            write_json(work / "request.json", request)
            job.update(state="queued", message=None, updated_at=now())
            write_json(folder / "job.json", job)
            self._active = asset.asset_id
            self._executor.submit(self._run, folder, work, job)
            return dict(job)

    def _run(self, folder: Path, work: Path, job: dict[str, Any]) -> None:
        try:
            with self._mutex:
                job.update(state="running", updated_at=now())
                write_json(folder / "job.json", job)
                env = dict(
                    os.environ,
                    CIVICCAST_AD_VOICE_DIR=str(self.voice),
                    HF_HUB_OFFLINE="1",
                    HF_HUB_DISABLE_TELEMETRY="1",
                    DO_NOT_TRACK="1",
                    OTEL_SDK_DISABLED="true",
                )
                gate = work / "output/WAIT"
                gate.parent.mkdir(exist_ok=True)
                if self._live_busy():
                    gate.touch()
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                log = (work / "worker.log").open("ab")
                try:
                    self._process = subprocess.Popen(  # noqa: S603 - internal argument list; no shell; media inputs are confined.
                        [
                            sys.executable,
                            "-m",
                            "civiccast.audio_description.worker",
                            str(work / "request.json"),
                        ],
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=flags,
                    )
                except BaseException:
                    log.close()
                    raise
            try:
                while True:
                    try:
                        busy = self._live_busy()
                    except Exception:
                        busy = True  # Unknown live state must not admit background inference.
                    wait = work / "output/WAIT"
                    wait.parent.mkdir(exist_ok=True)
                    if busy:
                        wait.touch()
                    else:
                        wait.unlink(missing_ok=True)
                    try:
                        code = self._process.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                log.close()
            with self._mutex:
                cancelled = (work / "CANCEL").exists()
                if code == 0 and not cancelled:
                    result = json.loads((work / "output/manifest.json").read_text(encoding="utf-8"))
                    job.update(state="complete", coverage=result["coverage"], message=None)
                    write_json(
                        folder / "current.json",
                        {
                            "job_id": job["job_id"],
                            "source_identity": job["source_identity"],
                            "trim_in": job["trim_in"],
                        },
                    )
                else:
                    job.update(
                        state="cancelled" if cancelled or code == 130 else "failed",
                        message="Generation stopped. Resume to reuse completed checkpoints."
                        if cancelled
                        else "Generation failed. Check the local model and narrator configuration, then resume. Private details are in the job log.",
                    )
                job["updated_at"] = now()
                write_json(folder / "job.json", job)
        except Exception:
            import traceback

            with (work / "worker.log").open("a", encoding="utf-8") as failure:
                failure.write(traceback.format_exc())
            with self._mutex:
                job.update(
                    state="failed",
                    updated_at=now(),
                    message="Audio description failed. Check the private worker log and local storage, then resume.",
                )
                write_json(folder / "job.json", job)
        finally:
            with self._mutex:
                self._active, self._process = None, None

    def cancel(self, asset_id: str) -> dict[str, Any]:
        with self._mutex:
            job = self.status(asset_id)
            if job["state"] not in ("queued", "running"):
                raise ValueError("This recording has no active generation job.")
            work = self._folder(asset_id) / job["job_id"]
            (work / "CANCEL").touch()
            (work / "output").mkdir(exist_ok=True)
            (work / "output/CANCEL").touch()
            job.update(
                message="Cancellation requested; generation stops at the next safe stage.",
                updated_at=now(),
            )
            write_json(self._folder(asset_id) / "job.json", job)
            return job

    def current(self, asset: Any) -> tuple[Path, dict[str, Any]] | None:
        folder = self._folder(asset.asset_id)
        pointer = folder / "current.json"
        if not pointer.is_file():
            return None
        record = json.loads(pointer.read_text(encoding="utf-8"))
        if record["source_identity"] != fingerprint(asset):
            return None
        root = (folder / record["job_id"] / "output").resolve()
        if not root.is_relative_to(folder):
            return None
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            return None
        return root, json.loads(manifest_path.read_text(encoding="utf-8"))

    def close(self) -> None:
        with self._mutex:
            self._closed = True
            if self._active:
                self.cancel(self._active)
            process = self._process
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Only this service's still-live worker and its descendants.
                # FFmpeg can be active during preparation; do not orphan it.
                import psutil

                children = []
                with suppress(psutil.NoSuchProcess):
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                for child in reversed(children):
                    with suppress(psutil.NoSuchProcess):
                        child.terminate()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                for child in children:
                    try:
                        child.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
        self._executor.shutdown(wait=True, cancel_futures=True)
        if self._owner:
            portalocker.unlock(self._owner)
            self._owner.close()
            self._owner = None
