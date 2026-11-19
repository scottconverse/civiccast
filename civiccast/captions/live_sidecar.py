# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Atomic live WebVTT publication for caption feed and decode-back workers."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from civiccast.captions.models import CaptionCue
from civiccast.captions.webvtt import render_webvtt

#: The channel caption-runtime states publishable to
#: ``<channel>/captions/runtime-status.json``. Named (rather than spelled out
#: inline) so producers -- ``civiccast.captions.tap_worker`` and the retention
#: policy -- can be typed against the same set instead of casting into it.
CaptionRuntimeState = Literal[
    "within-capacity",
    "delayed",
    "overloaded",
    "storage-refused",
    "paused",
    "disabled",
]


class LiveWebVttPublisher:
    """Publish stable cues atomically, optionally bounding a live delivery window.

    Limits are opt-in because archived English/Spanish sidecars also use this
    writer and must retain their complete reviewed caption tracks.
    """

    def __init__(
        self,
        active_path: Path,
        *,
        window_seconds: float | None = None,
        max_cues: int | None = None,
    ) -> None:
        if window_seconds is not None and (
            not math.isfinite(window_seconds) or window_seconds <= 0
        ):
            raise ValueError("caption history window must be finite and positive")
        if max_cues is not None and max_cues < 1:
            raise ValueError("caption history cue limit must be positive")
        self.active_path = active_path.expanduser().resolve()
        self.window_seconds = window_seconds
        self.max_cues = max_cues
        # #342: cues from a publish the replace refused. They are carried into
        # the next publish (unless that publish names the same cue_id), so a
        # refused write delays a caption but never drops one.
        self._unwritten: list[CaptionCue] = []

    def reset(self) -> None:
        """Remove stale cues at worker startup while keeping a valid WebVTT file."""

        self._unwritten = []
        self.publish([])

    def publish(self, cues: list[CaptionCue]) -> None:
        """Replace the sidecar with cues inside its configured delivery bounds.

        If the replace is refused after every retry, the error is raised as
        before, and these cues are kept for the next publish (#342)."""

        if self._unwritten:
            named = {cue.cue_id for cue in cues}
            cues = [*(cue for cue in self._unwritten if cue.cue_id not in named), *cues]
        selected = cues
        if selected and self.window_seconds is not None:
            cutoff = max(cue.end_seconds for cue in selected) - self.window_seconds
            # Keep a cue crossing the cutoff; do not clip its speech envelope.
            selected = [cue for cue in selected if cue.end_seconds > cutoff]
        if self.max_cues is not None:
            selected = sorted(
                selected, key=lambda cue: (cue.start_seconds, cue.end_seconds, cue.cue_id)
            )[-self.max_cues :]
        try:
            _atomic_write_text(self.active_path, render_webvtt(selected))
        except OSError:
            self._unwritten = list(cues)
            raise
        self._unwritten = []


def active_caption_sidecar(work_dir: Path, channel_id: str) -> Path:
    """Return the shared producer/feed/proof path for one channel."""

    return work_dir / channel_id / "captions" / "active.vtt"


def caption_runtime_status_path(work_dir: Path, channel_id: str) -> Path:
    """Return the operator/support status path for one live-caption channel."""

    return work_dir / channel_id / "captions" / "runtime-status.json"


def publish_caption_runtime_status(
    work_dir: Path,
    channel_id: str,
    *,
    state: CaptionRuntimeState,
    backlog_segments: int,
    max_backlog_segments: int,
    refusal_reason: str | None = None,
    resume_in_seconds: float | None = None,
    consecutive_overloads: int | None = None,
    worker_heartbeat_at: datetime | None = None,
    last_input_at: datetime | None = None,
    last_processed_at: datetime | None = None,
    pending_since_at: datetime | None = None,
    inference_started_at: datetime | None = None,
    audio_signal: Literal["digital-silence", "audio-present", "unknown"] | None = None,
    provider_state: str | None = None,
    provider_retry_in_seconds: int | None = None,
    inference_inflight: bool = False,
) -> Path:
    """Atomically publish capacity state without implying decode-back readiness.

    ``paused`` is the caption tap's backoff state
    (:mod:`civiccast.captions.tap_backoff`): this channel overloaded, its ASR
    is suspended for ``resume_in_seconds``, and live captions are off for that
    window so playout keeps the CPU. It carries ``resume_in_seconds`` and
    ``consecutive_overloads`` so the operator sees BOTH that captions stopped
    and when they will be attempted again -- an ``overloaded`` snapshot alone
    never said whether anything would happen next.
    """

    path = caption_runtime_status_path(work_dir, channel_id)
    if state in {"storage-refused", "disabled"}:
        LiveWebVttPublisher(active_caption_sidecar(work_dir, channel_id)).reset()
    payload: dict[str, object] = {
        "backlog_segments": backlog_segments,
        "channel_id": channel_id,
        "max_backlog_segments": max_backlog_segments,
        "state": state,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if worker_heartbeat_at is not None:
        payload.update(
            {
                "worker_heartbeat_at": worker_heartbeat_at.isoformat(),
                "last_input_at": last_input_at.isoformat() if last_input_at else None,
                "last_processed_at": (last_processed_at.isoformat() if last_processed_at else None),
                "pending_since_at": pending_since_at.isoformat() if pending_since_at else None,
                "inference_started_at": (
                    inference_started_at.isoformat() if inference_started_at else None
                ),
                "audio_signal": audio_signal or "unknown",
                "inference_inflight": bool(inference_inflight),
            }
        )
        if provider_state is not None:
            payload["provider_state"] = provider_state
        if provider_retry_in_seconds is not None:
            payload["provider_retry_in_seconds"] = max(0, int(provider_retry_in_seconds))
    if refusal_reason is not None:
        payload["refusal_reason"] = refusal_reason
    if resume_in_seconds is not None:
        payload["resume_in_seconds"] = round(float(resume_in_seconds), 1)
    if consecutive_overloads is not None:
        payload["consecutive_overloads"] = int(consecutive_overloads)
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def reset_existing_live_sidecars(work_dir: Path) -> None:
    """Fail closed after restart by replacing every prior active sidecar with empty VTT."""

    root = work_dir.expanduser()
    if not root.is_dir():
        return
    for path in root.glob("*/captions/active.vtt"):
        if path.is_file():
            LiveWebVttPublisher(path).reset()


#: Bounded retry for the live-sidecar atomic replace.
#:
#: Field defect (beta.9 three-channel ladder, 2026-09-19): the installed
#: service logged PermissionError [WinError 5] from
#: temporary.replace(destination) three times in ~8 h. On Windows a rename can
#: fail with ERROR_ACCESS_DENIED when another process (real-time virus scanning
#: or the search indexer are the live candidates here) holds a transient handle
#: on the freshly-fsynced temp file. A short bounded retry shrinks that window.
#:
#: This is a MITIGATION, not a fix for the trigger. It cannot recover a
#: permanent ACL/lock problem and it does not remove the external handle; if
#: every attempt is refused the original error is re-raised so the caller can
#: record it. A failure must never be swallowed into a silent success.
#:
#: #342 (rc.6, 5060 Ti, 2026-11-14 12:04:54): three attempts 75 ms apart
#: (about 0.15 s in all) still gave up once in about 5 hours, and that caption
#: update was lost. The station's own readers of ``active.vtt`` (the cable
#: embed feed and the decode-back proof, ``caption_embed._read_timed_text``)
#: open it without delete sharing, and Windows refuses a rename onto a file
#: that is open that way. Those readers retry up to 2 x 50 ms themselves, so
#: the writer must outlast one full reader retry cycle, not race it. The wait
#: now grows (25, 50, 100, 200, 400, 800 ms): a quick release still costs
#: 25 ms, and a held handle gets about 1.6 s before the error is raised.
_ATOMIC_REPLACE_BACKOFF_SCHEDULE_SECONDS: tuple[float, ...] = (0.025, 0.05, 0.1, 0.2, 0.4, 0.8)
_ATOMIC_REPLACE_ATTEMPTS = len(_ATOMIC_REPLACE_BACKOFF_SCHEDULE_SECONDS) + 1


def _atomic_write_text(destination: Path, content: str) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    owned_descriptor: int | None = descriptor
    try:
        handle = os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        )
        # fdopen now owns the descriptor. Its context closes it even when a
        # write fails; replacement cleanup must never close a reused number.
        owned_descriptor = None
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: OSError | None = None
        for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
            try:
                temporary.replace(destination)
                return
            except OSError as error:
                # Transient handle on the temp/active file (e.g. real-time
                # scanner or indexer). Retry a bounded number of times, then
                # surface the original failure to the caller.
                last_error = error
                if attempt < len(_ATOMIC_REPLACE_BACKOFF_SCHEDULE_SECONDS):
                    time.sleep(_ATOMIC_REPLACE_BACKOFF_SCHEDULE_SECONDS[attempt])
        assert last_error is not None
        raise last_error
    except Exception:
        if owned_descriptor is not None:
            with suppress(OSError):
                os.close(owned_descriptor)
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "CaptionRuntimeState",
    "LiveWebVttPublisher",
    "active_caption_sidecar",
    "caption_runtime_status_path",
    "publish_caption_runtime_status",
    "reset_existing_live_sidecars",
]
