# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Per-ON_AIR caption decode-back proof loop (S11a).

The embed side (GStreamer-native ``cccombiner``/``h264ccinserter``) inserts CEA-708
SEI; THIS loop is what proves it actually survived to the *emitted* stream and flips
``caption_status`` to ``on``. Each scan, for every ON_AIR channel: capture a short
segment of the emitted stream, decode its embedded captions, compare to the channel's
expected caption cues, and persist an ``EgressCaptionProofSample`` (which
``build_caption_status_provider`` reads — fail-closed: a fresh PASS → ``on``, anything
else → ``not-verified``).

The loop logic, cue selection, and persistence are unit-tested here with fakes. The
two live edges — capturing the emitted broadcast stream and the binding to the channel
caption pipeline's cues — are WSL/LPM-validated (a real live udp/srt egress + a real
caption source). Follows the ``ThreadSupervisor`` ``run_forever``/``run_once`` shape.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from civiccast.captions.models import CaptionCue
from civiccast.egress.caption_embed import CAPTION_EMBED_PROOF_BOUNDARY
from civiccast.egress.caption_proof import (
    DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS,
    DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS,
    CaptionMode,
    Clock,
    FfmpegRunner,
    _run_bounded_ffmpeg,
    sample_caption_decode_back,
)
from civiccast.egress.models import EgressCaptionProofSample
from civiccast.egress.sinks import HlsSink
from civiccast.egress.store import EgressStore
from civiccast.stream._ffmpeg import FfmpegNotFoundError, ffmpeg_seconds, run_ffmpeg

_LOG = logging.getLogger(__name__)

OnAirChannelsProvider = Callable[[], list[str]]
SegmentCapture = Callable[[str], Path | None]
ExpectedCuesProvider = Callable[[str], list[CaptionCue]]


@dataclass(frozen=True)
class CaptionProofScanResult:
    """Outcome of one ``run_once`` scan."""

    scanned: int = 0
    passed: int = 0
    failed: int = 0
    skipped_no_capture: int = 0
    channels: tuple[str, ...] = field(default_factory=tuple)


class CaptionProofWorker:
    """Sample caption decode-back for ON_AIR channels and persist the proof.

    All collaborators are injected so the loop is unit-testable without GStreamer,
    ffmpeg, or a live stream:

    * ``on_air_channels`` — the channels currently ON_AIR (and embedding captions).
    * ``capture_segment`` — capture a short emitted-stream segment for a channel,
      or None if no segment is available (a timestamped unavailable sample is
      persisted; ``caption_status`` stays not-verified until a real capture proves a PASS).
    * ``expected_cues_provider`` — the cues the channel is embedding (the channel
      caption pipeline: review queue / ASR tap / sidecar). Empty → the sample is a
      FAIL with ``NO_EXPECTED_CUES`` (we never fabricate a PASS).
    * ``is_enabled`` — the operator's live-captions switch
      (``StationProfile.live_captions_enabled`` via
      ``resolve_live_captions_enabled``), consulted on EVERY scan. With live
      captions off no embed leg is built and the tap blanks every sidecar, so a
      scan would only spend a 6 s ffmpeg capture per channel every 30 s to write
      a guaranteed ``NO_EXPECTED_CUES`` FAIL row (round-2 review MAJOR 3). The
      scan short-circuits instead; flipping the switch on later resumes proofs
      on the next cycle with no control-plane restart. Default: always enabled.
    """

    def __init__(
        self,
        *,
        store: EgressStore,
        on_air_channels: OnAirChannelsProvider,
        capture_segment: SegmentCapture,
        expected_cues_provider: ExpectedCuesProvider,
        mode: CaptionMode = "cea-708",
        runner: FfmpegRunner = run_ffmpeg,
        clock: Clock = lambda: datetime.now(UTC),
        is_enabled: Callable[[], bool] | None = None,
        ffmpeg_timeout_seconds: float | None = DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS,
        align_transport_clock: bool = False,
        require_clean_page_load: bool = False,
    ) -> None:
        self._store = store
        self._on_air_channels = on_air_channels
        self._capture_segment = capture_segment
        self._expected_cues_provider = expected_cues_provider
        self._mode = mode
        self._runner = runner
        self._clock = clock
        self._is_enabled = is_enabled or (lambda: True)
        self._ffmpeg_timeout_seconds = ffmpeg_timeout_seconds
        self._align_transport_clock = align_transport_clock
        self._require_clean_page_load = require_clean_page_load
        self._disabled_announced = False

    def run_forever(
        self,
        *,
        poll_seconds: float = 30.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Run the scan loop until ``stop_event`` is set; scan errors are logged."""
        while stop_event is None or not stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                _LOG.exception("Caption proof scan failed; continuing.")
            if stop_event is None:
                threading.Event().wait(poll_seconds)  # pragma: no cover - loop shape
            else:
                stop_event.wait(poll_seconds)

    def run_once(self) -> CaptionProofScanResult:
        """Sample every ON_AIR channel once and persist each proof sample.

        Returns an empty result -- no capture, no DB row -- while the operator
        has live captions switched off (see ``is_enabled``)."""
        if not self._is_enabled():
            if not self._disabled_announced:
                _LOG.info(
                    "Live captions are switched off for this station "
                    "(StationProfile.live_captions_enabled); the caption decode-back "
                    "proof is idle until they are switched on."
                )
                self._disabled_announced = True
            return CaptionProofScanResult()
        self._disabled_announced = False
        passed = 0
        failed = 0
        skipped = 0
        sampled: list[str] = []
        for channel_id in self._on_air_channels():
            try:
                segment = self._capture_segment(channel_id)
            except (OSError, FfmpegNotFoundError):
                _LOG.warning(
                    "Caption proof capture unavailable for channel %s", channel_id, exc_info=True
                )
                segment = None
            if segment is None:
                # FAIL is the existing storage's fail-closed verdict, not a
                # claim that a decoder ran and found missing/wrong captions.
                self._store.append_caption_proof_sample(
                    EgressCaptionProofSample(
                        channel_id=channel_id,
                        sampled_at=self._clock(),
                        status="FAIL",
                        caption_status="not-verified",
                        mode=self._mode,
                        decoder_name="not-run",
                        proof_boundary=CAPTION_EMBED_PROOF_BOUNDARY,
                        blocker="EGRESS_CAPTION_DECODE_BACK_CAPTURE_UNAVAILABLE",
                    )
                )
                skipped += 1
                continue
            sample = sample_caption_decode_back(
                channel_id=channel_id,
                emitted_stream_path=segment,
                expected_cues=self._expected_cues_provider(channel_id),
                mode=self._mode,
                runner=self._runner,
                clock=self._clock,
                timeout_seconds=self._ffmpeg_timeout_seconds,
                align_transport_clock=self._align_transport_clock,
                require_clean_page_load=self._require_clean_page_load,
            )
            self._store.append_caption_proof_sample(sample)
            sampled.append(channel_id)
            if sample.status == "PASS":
                passed += 1
            else:
                failed += 1
        return CaptionProofScanResult(
            scanned=passed + failed,
            passed=passed,
            failed=failed,
            skipped_no_capture=skipped,
            channels=tuple(sampled),
        )


# --- live wiring (WSL/LPM edge) ------------------------------------------------

# Sink schemes whose emitted stream is captured live by ffmpeg for decode-back.
_CAPTURE_SINK_SCHEMES = ("udp", "srt")


def _emitted_stream_target(store: EgressStore, channel_id: str) -> tuple[str, bool] | None:
    """``(uri, is_file)`` for the channel's emitted stream to decode back, or None.

    Prefers a file/local-ts sink (the written TS — read its tail); else a live
    udp/srt broadcast sink (captured by ffmpeg), else the advertised local HLS
    manifest. HLS is a live input, not a seekable growing TS file. rtmp/sdi are
    not TS-decode targets."""
    config = store.get_config(channel_id)
    if config is None:
        return None
    for sink in config.sinks:
        if sink.kind in ("file", "local-ts"):
            parsed = urlsplit(sink.uri)
            return (parsed.path if parsed.scheme == "file" else sink.uri, True)
    for sink in config.sinks:
        if urlsplit(sink.uri).scheme.lower() in _CAPTURE_SINK_SCHEMES:
            return (sink.uri, False)
    for sink in config.sinks:
        if sink.kind == "hls":
            # Read exactly the manifest served to residents, never the muxer's
            # private staging playlist (holding it can block Windows renames).
            return (HlsSink(sink).manifest_target(), False)
    return None


def capture_emitted_segment(
    store: EgressStore,
    channel_id: str,
    *,
    work_dir: Path,
    capture_seconds: float = DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS,
    runner: FfmpegRunner = run_ffmpeg,
    timeout_seconds: float | None = DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS,
) -> Path | None:
    """Capture a short, bounded segment of the channel's emitted stream (WSL/LPM edge).

    A live udp/srt or advertised HLS sink is captured for ``capture_seconds``; a file/local-ts sink's
    LAST ``capture_seconds`` are copied (``-sseof``) so a 24/7 channel's growing file is
    never decoded whole. Returns the segment path, or None if there is nothing to
    capture / the capture produced no bytes (then proof is unavailable, fail-closed)."""
    target = _emitted_stream_target(store, channel_id)
    if target is None:
        return None
    uri, is_file = target
    is_hls = not is_file and urlsplit(uri).scheme.lower() not in _CAPTURE_SINK_SCHEMES
    if is_hls:
        from civiccast.egress.hls_relay import _last_segment

        # Match relay progress: neither rewritten tags nor a touched manifest
        # proves fresh media. Even the first capture must witness advancement.
        prior_segment = _last_segment(Path(uri))
        if prior_segment is None:
            return None
    out = work_dir / channel_id / "caption-proof" / "segment.ts"
    out.parent.mkdir(parents=True, exist_ok=True)
    seconds = ffmpeg_seconds(capture_seconds)
    if is_file:
        if not Path(uri).exists():
            return None
        args = ["-y", "-hide_banner", "-nostats", "-sseof", f"-{seconds}", "-i", uri]
    else:
        args = ["-y", "-hide_banner", "-nostats", "-i", uri]
        if is_hls:
            # Otherwise ffmpeg can immediately copy six cached seconds from a
            # rolling window before the healthy publisher has time to advance.
            args.insert(args.index("-i"), "-re")
            # Read from the first listed segment. Joined at FFmpeg's default live
            # point (3 segments from the end) the shipped build measured the input
            # -t from the window start and wrote ZERO bytes with exit 0, so every
            # proof sample was "capture unavailable" (RC4 host run 2026-11-04).
            args[args.index("-i") : args.index("-i")] = ["-live_start_index", "0"]
    # Preserve the emitted transport clock, including wrap, rather than
    # replacing the capture's epoch with ffmpeg's default near-zero clock.
    args.insert(0, "-copyts")
    # Input duration is relative to input start. Output -t with -copyts would
    # stop immediately when a days-old PTS already exceeds six seconds.
    args[args.index("-i") : args.index("-i")] = ["-t", seconds]
    args += ["-c", "copy", "-mpegts_copyts", "1", "-f", "mpegts", str(out)]
    with contextlib.suppress(OSError):
        # Never let a failed/partial proof capture be mistaken for the prior
        # successful segment at this fixed path.
        out.unlink(missing_ok=True)
    try:
        result = _run_bounded_ffmpeg(runner, args, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired:
        _LOG.warning(
            "Caption proof capture timed out after %ss for channel %s; "
            "failing closed with no segment.",
            timeout_seconds,
            channel_id,
        )
        with contextlib.suppress(OSError):
            out.unlink(missing_ok=True)
        return None
    if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        return None
    if is_hls:
        latest_segment = _last_segment(Path(uri))
        if latest_segment is None or latest_segment == prior_segment:
            with contextlib.suppress(OSError):
                out.unlink(missing_ok=True)
            return None
    return out


_PRESENTATION_CLOCK = "cea608-display-control-pts-plus-mpegts-base"
#: F11: a page is still loading about half the time on a live channel, and it
#: displays within ~1.5 s (measured: 12 reads over 18 s, 6 pending). Re-read a
#: bounded number of times instead of failing the whole sample on the first read.
_RECEIPT_READ_ATTEMPTS = 8
_RECEIPT_READ_DELAY_SECONDS = 0.6


def _settled_receipt_cues(document: object, channel_id: str) -> list[CaptionCue] | None:
    """Cues from a receipt with no page still loading; None while one is; [] if invalid."""

    if (
        not isinstance(document, dict)
        or document.get("clock") != _PRESENTATION_CLOCK
        or document.get("errors")
    ):
        return []
    counts = [
        document.get(name)
        for name in ("encoded_page_count", "displayed_page_count", "retired_page_count")
    ]
    if any(type(count) is not int or count < 0 for count in counts):
        return []
    encoded, displayed, retired = cast(list[int], counts)
    if document.get("pending_page_count") != 0 or encoded != displayed + retired:
        # A completed prefix must not hide a queued page that never displayed.
        # Only explicit program fencing retires pending text, so wait for it.
        return None
    pages = document["pages"]
    if not isinstance(pages, list) or len(pages) > 512:
        return []
    return [
        CaptionCue(
            cue_id=f"{channel_id}-delivery-{index}",
            start_seconds=page["start_seconds"],
            end_seconds=page["end_seconds"],
            text=page["text"],
            confidence=1.0,
            low_confidence=False,
        )
        for index, page in enumerate(pages)
    ]


def read_presentation_receipt(
    path: Path,
    *,
    channel_id: str,
    attempts: int = _RECEIPT_READ_ATTEMPTS,
    delay_seconds: float = _RECEIPT_READ_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[CaptionCue]:
    """Expected cues from the encoder delivery receipt, waiting out a loading page.

    Fails closed (``[]``) on an unreadable, foreign-clock or errored receipt at
    once, and on a receipt whose queue never settles within the bounded reads --
    a stuck queue is never read as settled."""

    for attempt in range(max(1, attempts)):
        try:
            cues = _settled_receipt_cues(json.loads(path.read_text(encoding="utf-8")), channel_id)
        except (OSError, ValueError, KeyError, TypeError):
            return []
        if cues is not None:
            return cues
        if attempt < attempts - 1:
            sleep(delay_seconds)
    return []


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_caption_proof_worker(
    session_factory: SessionFactory,
    *,
    work_dir: Path | None = None,
    capture_seconds: float = DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS,
    mode: CaptionMode = "cea-708",
    caption_sidecar_for: Callable[[str], Path] | None = None,
    runner: FfmpegRunner = run_ffmpeg,
    is_enabled: Callable[[], bool] | None = None,
    ffmpeg_timeout_seconds: float | None = DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS,
) -> CaptionProofWorker:
    """Wire the production caption proof worker (Postgres store + live providers).

    ``on_air_channels`` reads channel state; ``capture_segment`` taps the emitted
    stream (live edge); ``expected_cues_provider`` loads encoder display/erase
    times from ``<work_dir>/<channel>/captions/presentation.json``. Source
    sidecars retain audio times and cannot substitute for these transport times.
    ``caption_sidecar_for`` remains a compatibility argument; production proof
    always uses the worker delivery receipt. Missing receipt or capture clock
    fails closed (caption_status stays not-verified). ``is_enabled`` is the
    operator's live-captions switch, re-read every scan (``civiccast.app`` passes
    ``resolve_live_captions_enabled``)."""
    from civiccast.egress.automation import default_egress_work_dir
    from civiccast.egress.store import PostgresEgressStore

    store = PostgresEgressStore(session_factory)
    resolved_work_dir = (work_dir or default_egress_work_dir()).expanduser()

    def _on_air() -> list[str]:
        channels: list[str] = []
        for config in store.list_configs():
            state_row = store.read_state(config.channel_id)
            if state_row is not None and state_row.state == "ON_AIR":
                channels.append(config.channel_id)
        return channels

    def _capture(channel_id: str) -> Path | None:
        return capture_emitted_segment(
            store,
            channel_id,
            work_dir=resolved_work_dir,
            capture_seconds=capture_seconds,
            runner=runner,
            timeout_seconds=ffmpeg_timeout_seconds,
        )

    def _expected(channel_id: str) -> list[CaptionCue]:
        # appsrc PTS is when pop-on memory starts LOADING, not when EOC displays
        # it. Compare independently decoded TS against actual pre-mux EOC/EDM
        # timing, preserving the existing tolerance and unknown-clock refusal.
        return read_presentation_receipt(
            resolved_work_dir / channel_id / "captions" / "presentation.json",
            channel_id=channel_id,
        )

    return CaptionProofWorker(
        store=store,
        on_air_channels=_on_air,
        capture_segment=_capture,
        expected_cues_provider=_expected,
        mode=mode,
        runner=runner,
        is_enabled=is_enabled,
        ffmpeg_timeout_seconds=ffmpeg_timeout_seconds,
        align_transport_clock=True,
        require_clean_page_load=True,
    )
