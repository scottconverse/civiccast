# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Caption tap worker tests (Beta sprint B6, decision #1 option A).

The worker consumes the egress audio fork's rolling WAV segments
(``<tap_root>/<channel_id>/chunk-NNNNNN.wav``), feeds them through the
existing live caption seam (pipeline → stabilization → durable review
queue), and moves consumed segments aside so a scan never double-feeds.
House worker shape: env settings with fail-fast ``from_env``, injected
runtime, ``run_once``/``run_forever`` survive-and-log loop.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
import time
import uuid
import wave
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest

from civiccast.captions import runtime as caption_runtime_module
from civiccast.captions import tap_batch_diagnostic as tbd
from civiccast.captions.models import (
    AudioChunk,
    CaptionCue,
    CaptionHypothesis,
    CaptionWord,
    CustomVocabulary,
)
from civiccast.captions.review import InMemoryCaptionReviewStore
from civiccast.captions.runtime import FasterWhisperRuntime
from civiccast.captions.tap import TAP_SAMPLE_RATE_HZ
from civiccast.captions.tap_backoff import DEFAULT_BASE_BACKOFF_SECONDS, CaptionBackoffPolicy
from civiccast.captions.tap_worker import (
    CaptionTapScanResult,
    CaptionTapWorker,
    CaptionTapWorkerSettings,
    build_tap_worker,
    default_max_channel_workers,
)
from civiccast.egress.caption_embed import load_caption_cues_from_timed_text
from civiccast.egress.caption_feed import CaptionFeedWorker


class _ScriptedRuntime:
    """Yields one hypothesis per chunk; text is per-call scripted."""

    def __init__(self, text: str = "the council will come to order") -> None:
        self.text = text
        self.seen_chunks: list[AudioChunk] = []

    def transcribe(
        self,
        chunks: Iterable[AudioChunk],
        vocabulary: CustomVocabulary | None = None,
    ) -> Iterable[CaptionHypothesis]:
        for chunk in chunks:
            self.seen_chunks.append(chunk)
            yield CaptionHypothesis(
                source_id=f"{chunk.chunk_id}-tap",
                start_seconds=chunk.start_seconds,
                end_seconds=chunk.end_seconds,
                text=self.text,
                confidence=0.9,
            )


class _ConcurrencyProbeRuntime(_ScriptedRuntime):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def transcribe(
        self,
        chunks: Iterable[AudioChunk],
        vocabulary: CustomVocabulary | None = None,
    ) -> Iterable[CaptionHypothesis]:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            time.sleep(0.05)
            yield from super().transcribe(chunks, vocabulary=vocabulary)
        finally:
            with self._lock:
                self._active -= 1


class _CudaConcurrencyProbeRuntime(_ConcurrencyProbeRuntime):
    """Three-worker CUDA shape used by the native live-caption runtime."""

    num_workers = 3

    @staticmethod
    def on_cuda() -> bool:
        return True


class _BlockingRuntime(_ScriptedRuntime):
    """Hold ASR open so the scan/gate relationship can be tested directly."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe(
        self,
        chunks: Iterable[AudioChunk],
        vocabulary: CustomVocabulary | None = None,
    ) -> Iterable[CaptionHypothesis]:
        self.started.set()
        assert self.release.wait(timeout=10.0), "test runtime was not released"
        yield from super().transcribe(chunks, vocabulary=vocabulary)


class _WhistleBacklogRuntime(_BlockingRuntime):
    """Blocking runtime with Whistle's three-channel backlog allowance."""

    caption_backlog_grace_seconds = 210.0


class _FakeClock:
    """A ``time.monotonic``-shaped clock the test drives explicitly.

    The backoff windows are minutes long; nothing here is allowed to sleep.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def _write_wav(path: Path, *, seconds: float = 1.0, silence: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = int(TAP_SAMPLE_RATE_HZ * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(TAP_SAMPLE_RATE_HZ)
        handle.writeframes((b"\x00\x00" if silence else b"\x01\x00") * frame_count)


#: Mirrors the shipped ``tap_worker.DEFAULT_OVERLOAD_PERSISTENCE_SCANS``. It is a
#: literal here, not an import, so the U11 behaviour tests can be run against the
#: PRE-U11 module (that is what makes their red real rather than a collection
#: error). ``test_the_persistence_window_defaults_and_parses_from_the_environment``
#: imports the shipped constant and asserts the two are equal, so this copy
#: cannot drift.
_OVERLOAD_PERSISTENCE_WINDOW = 15

#: Mirror the shipped U23 catch-up knobs, by the same rule as the window above:
#: literals, so these tests can run against a module that has no catch-up at all
#: (their red is then an assertion, not a collection error). The defaults test in
#: ``TestCaptionTapCatchUp`` imports the shipped constants and asserts equality,
#: so the mirrors cannot drift.
_CATCH_UP_SHED_LIMIT = 3
_CATCH_UP_SHED_WINDOW_SECONDS = 300.0


def _worker(  # type: ignore[no-untyped-def]
    tap_root: Path,
    runtime: _ScriptedRuntime,
    store: InMemoryCaptionReviewStore,
    *,
    segment_seconds: float = 1.0,
    atomic_segments: bool = False,
    max_channel_workers: int | None = None,
    backoff_policy: CaptionBackoffPolicy | None = None,
    is_enabled: Callable[[], bool] | None = None,
    monotonic: Callable[[], float] | None = None,
    overload_persistence_scans: int = _OVERLOAD_PERSISTENCE_WINDOW,
    catch_up_shed_limit: int = _CATCH_UP_SHED_LIMIT,
    catch_up_shed_window_seconds: float = _CATCH_UP_SHED_WINDOW_SECONDS,
    batch_diagnostic: object | None = None,
):
    return CaptionTapWorker(
        retain_review_evidence=True,
        tap_root=tap_root,
        caption_work_dir=tap_root.parent / "egress",
        runtime=runtime,
        review_store=store,
        segment_seconds=segment_seconds,
        atomic_segments=atomic_segments,
        max_channel_workers=max_channel_workers,
        backoff_policy=backoff_policy,
        is_enabled=is_enabled,
        monotonic=monotonic,
        overload_persistence_scans=overload_persistence_scans,
        catch_up_shed_limit=catch_up_shed_limit,
        catch_up_shed_window_seconds=catch_up_shed_window_seconds,
        batch_diagnostic=batch_diagnostic,  # type: ignore[arg-type]
    )


def _active_vtt(tap_root: Path, channel_id: str) -> Path:
    return tap_root.parent / "egress" / channel_id / "captions" / "active.vtt"


def test_tap_worker_uses_embedded_audio_clock_instead_of_persistent_index(tmp_path: Path) -> None:
    from civiccast.egress.gst.audio_tap import RollingWavSegmentWriter

    tap_root = tmp_path / "tap"
    channel = tap_root / "education"
    channel.mkdir(parents=True)
    worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())
    _write_wav(channel / "chunk-000050.wav")
    writer = RollingWavSegmentWriter(channel, segment_seconds=0.01, sample_rate_hz=1000)
    writer.write_pcm_s16le(b"\x01\x00" * 10, running_time_seconds=1.375)
    assert writer.close() == "drained"
    chunk = worker._read_chunk("education", 51, channel / "chunk-000051.wav")
    assert chunk is not None
    assert chunk.start_seconds == 1.375
    assert chunk.end_seconds == pytest.approx(1.385)
    legacy = worker._read_chunk("education", 50, channel / "chunk-000050.wav")
    assert legacy is not None and legacy.start_seconds == 50.0


def test_program_boundary_refuses_delayed_asr_and_feeds_only_incoming_captions(
    tmp_path: Path,
) -> None:
    tap_root = tmp_path / "tap"
    for channel in ("public", "government"):
        (tap_root / channel).mkdir(parents=True)
    worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())
    worker.run_once()
    cue = CaptionCue(
        cue_id="speech",
        start_seconds=1,
        end_seconds=3,
        text="Outgoing speech",
        confidence=1,
        low_confidence=False,
    )
    old_generation = worker._session_generation.get("public", 0)
    assert worker.publish_for_current_session("public", old_generation, [cue])
    assert worker.publish_for_current_session("government", 0, [cue])
    (tap_root / "public" / "program-boundary.json").write_text(
        json.dumps({"token": "incoming", "first_segment": 2})
    )
    # A late ASR completion is refused even before the next scan sees the marker.
    assert not worker.publish_for_current_session("public", old_generation, [cue])
    worker.run_once()
    assert "Outgoing speech" not in _active_vtt(tap_root, "public").read_text()
    assert "Outgoing speech" in _active_vtt(tap_root, "government").read_text()
    incoming = cue.model_copy(update={"text": "Incoming speech"})
    assert worker.publish_for_current_session(
        "public", worker._session_generation["public"], [incoming]
    )
    calls: list[dict] = []
    feed = CaptionFeedWorker(
        work_dir=tmp_path / "egress",
        on_air_channels=lambda: ["public"],
        caption_cue_provider=lambda channel: load_caption_cues_from_timed_text(
            _active_vtt(tap_root, channel), source_id=channel
        ),
        program_token_provider=lambda _channel: "incoming",
        send_caption_cue=lambda *_args, **kwargs: calls.append(kwargs) or True,
    )
    assert feed.run_once().cues_sent == 1
    assert calls[0]["text"] == "Incoming speech"
    assert calls[0]["program_token"] == "incoming"


class _BoundaryDuringAsrRuntime(_ScriptedRuntime):
    """Starts a new programme session while ASR is running (r7 field shape)."""

    def __init__(self) -> None:
        super().__init__()
        self.worker: CaptionTapWorker | None = None

    def transcribe(
        self,
        chunks: Iterable[AudioChunk],
        vocabulary: CustomVocabulary | None = None,
    ) -> Iterable[CaptionHypothesis]:
        assert self.worker is not None
        self.worker._clear_channel_captions("public")
        yield from super().transcribe(chunks, vocabulary=vocabulary)


def test_boundary_during_asr_still_records_the_input_as_processed(tmp_path: Path) -> None:
    """RC4 soak, r7 2026-11-09: a programme boundary landing while a batch was in
    ASR left ``last_input_at`` ahead of ``last_processed_at`` with nothing in
    flight, so ``/health`` answered degraded / live captions ``unknown`` until the
    next chunk. ASR did finish that input; only publication is refused.
    """
    from datetime import datetime

    from civiccast.alerting.runtime_status import derive_live_caption_processing_status

    tap_root = tmp_path / "tap"
    runtime = _BoundaryDuringAsrRuntime()
    worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore(), atomic_segments=True)
    runtime.worker = worker
    _write_wav(tap_root / "public" / "chunk-000001.wav")

    worker.run_once()
    # Field order: the egress writes the new programme's marker, so the next scan
    # drops the outgoing chunk and publishes an idle status for the channel.
    (tap_root / "public" / "program-boundary.json").write_text(
        json.dumps({"token": "incoming", "first_segment": 2})
    )
    worker.run_once()

    payload = _runtime_status(tap_root, "public")
    assert payload["last_input_at"] is not None
    assert payload["last_processed_at"] is not None
    assert payload["last_processed_at"] >= payload["last_input_at"]
    status = derive_live_caption_processing_status(
        payload,
        egress_state="ON_AIR",
        captions_expected=True,
        now=datetime.fromisoformat(payload["worker_heartbeat_at"]),
    )
    assert status.processing_state != "waiting"
    # The refused batch published nothing into the new session.
    assert "council" not in _active_vtt(tap_root, "public").read_text(encoding="utf-8")


def _runtime_status(tap_root: Path, channel_id: str) -> dict:
    path = tap_root.parent / "egress" / channel_id / "captions" / "runtime-status.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_capacity_status_distinguishes_transient_and_sustained_backlog(tmp_path):
    tap_root = tmp_path / "tap"
    clock = _FakeClock()
    runtime = _WhistleBacklogRuntime()
    worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore(), monotonic=clock)
    for index in range(3):
        _write_wav(tap_root / "government" / f"chunk-{index:06d}.wav")
    worker._publish_status("government", state="within-capacity", backlog_segments=3)
    assert _runtime_status(tap_root, "government")["state"] == "delayed"
    for _ in range(_OVERLOAD_PERSISTENCE_WINDOW):
        worker._note_backlog_depth("government", 3)
    clock.advance(211)
    worker._publish_status("government", state="within-capacity", backlog_segments=3)
    assert _runtime_status(tap_root, "government")["state"] == "overloaded"
    worker._publish_status("government", state="within-capacity", backlog_segments=0)
    assert _runtime_status(tap_root, "government")["state"] == "within-capacity"


def test_idle_scan_publishes_processing_heartbeat_without_audio(tmp_path: Path) -> None:
    """An empty audio scan is observable without treating silence as a fault."""
    tap_root = tmp_path / "tap"
    (tap_root / "government").mkdir(parents=True)
    worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())
    worker.run_once()
    status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"
    assert status_path.is_file(), "the worker should publish a heartbeat even when no WAV is ready"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["worker_heartbeat_at"]
    assert payload["audio_signal"] == "unknown"
    assert payload["last_processed_at"] is None


def test_processed_digital_silence_is_reported_without_a_caption_failure(tmp_path: Path) -> None:
    tap_root = tmp_path / "tap"
    worker = _worker(
        tap_root,
        _ScriptedRuntime(),
        InMemoryCaptionReviewStore(),
        atomic_segments=True,
    )
    _write_wav(tap_root / "government" / "chunk-000001.wav", silence=True)

    result = worker.run_once()
    payload = _runtime_status(tap_root, "government")

    assert result.consumed_segments == 1
    assert payload["audio_signal"] == "digital-silence"
    assert payload["last_input_at"]
    assert payload["last_processed_at"]


def _drive_to_the_edge_of_the_overload_window(
    worker,  # type: ignore[no-untyped-def]
    tap_root: Path,
    channel_id: str,
    *,
    first_index: int = 0,
    per_scan: int = 4,
) -> int:
    """Feed the persistence window MINUS ONE consecutive over-limit scan.

    Every scan writes its own fresh segments, so the pending backlog is over
    ``max_backlog_segments`` on each of them -- the shape of a genuine,
    sustained slow-ASR collapse. It returns the next free chunk index, with the
    channel still un-paused: the caller's next ``run_once()`` is the scan that
    trips.

    U11 deliberately spares a ONE-SCAN overshoot (U10 measured 128 of 182 live
    trips firing at exactly 3 settled segments against a max of 2, i.e. the
    minimum possible overshoot), so every test that wants the historical
    "pause on the first overshoot" behaviour has to ask for the sustained shape
    explicitly, through here.
    """

    index = first_index
    for scan in range(_OVERLOAD_PERSISTENCE_WINDOW - 1):
        for _ in range(per_scan):
            _write_wav(tap_root / channel_id / f"chunk-{index:06d}.wav")
            index += 1
        result = worker.run_once()
        assert result.overloaded_channels == (), f"scan {scan} paused inside the window"
        assert result.paused_channels == (), f"scan {scan} paused inside the window"
    return index


def _drive_sustained_episode(
    worker,  # type: ignore[no-untyped-def]
    tap_root: Path,
    channel_id: str,
    *,
    first_index: int = 0,
    per_scan: int = 4,
    final_files: int = 4,
) -> tuple[CaptionTapScanResult, int]:
    """Drive ONE sustained over-limit episode to the scan the gate acts on.

    Returns ``(acting_scan_result, next_free_chunk_index)``.

    The shape is the one the gate keys on -- over ``max_backlog_segments`` on
    every scan of the whole persistence window -- and it is the shape a stalled
    ASR produces on a live station: audio keeps arriving at the cadence while
    transcription is not retiring it, so the settled backlog stays over the
    limit scan after scan.

    Under U23 the acting scan SHEDS (catch-up) unless the channel has already
    spent its shed budget; the caller reads ``paused_channels`` to tell which.
    """

    index = _drive_to_the_edge_of_the_overload_window(
        worker, tap_root, channel_id, first_index=first_index, per_scan=per_scan
    )
    for path in (tap_root / channel_id).glob("chunk-*.wav"):
        path.unlink()
    for _ in range(final_files):
        _write_wav(tap_root / channel_id / f"chunk-{index:06d}.wav")
        index += 1
    return worker.run_once(), index


def _trip_overload(
    worker,  # type: ignore[no-untyped-def]
    tap_root: Path,
    channel_id: str,
    *,
    first_index: int = 0,
    per_scan: int = 4,
    final_files: int = 4,
    shed_limit: int = _CATCH_UP_SHED_LIMIT,
) -> tuple[CaptionTapScanResult, int]:
    """Drive one channel all the way into its PAUSE state.

    Returns ``(tripping_scan_result, next_free_chunk_index)``.

    U23 changed how a pause is earned. A channel that stays over the limit for
    one whole persistence window no longer pauses -- it SHEDS its oldest settled
    audio and keeps captioning, so that a GPU stall costs seconds of audio
    instead of minutes of no captions. The pause is now reserved for a channel
    that does that ``shed_limit`` times inside the shed window, i.e. one that has
    been over the limit for whole persistence windows over and over. This helper
    therefore drives whole EPISODES and asserts that every episode before the
    last one shed instead of pausing -- a trip that skipped the shedding would
    be a real defect, not a shortcut.

    ``shed_limit`` must match the worker's own ``catch_up_shed_limit``;
    ``shed_limit=0`` (catch-up off) reproduces the pre-U23 "pause on the first
    persistent overshoot" behaviour the ladder tests are written against.

    ``final_files`` sets the queue the ACTING scan actually sees: the pile the
    window accumulated is dropped first, so a caller asserting exact
    ``dropped_overload_segments``/``backlog_segments`` numbers is asserting the
    gate's behaviour at the trip, not how much audio the window happened to pile
    up. The over-limit STREAK -- which is all the gate keys on -- is untouched by
    that cleanup, because it is only ever updated inside ``run_once``.
    """

    index = first_index
    for episode in range(shed_limit):
        shed, index = _drive_sustained_episode(
            worker,
            tap_root,
            channel_id,
            first_index=index,
            per_scan=per_scan,
            final_files=final_files,
        )
        assert shed.paused_channels == (), (
            f"episode {episode} paused the channel; inside its {shed_limit}-shed "
            "budget a sustained overshoot must CATCH UP instead"
        )
        assert shed.overloaded_channels == (), f"episode {episode} paused the channel"
        assert shed.dropped_overload_segments > 0, (
            f"episode {episode} neither paused nor shed: the over-limit audio vanished "
            "without being reported"
        )
        assert shed.consumed_segments > 0, (
            f"episode {episode} shed without resuming: catch-up must keep captioning "
            "from the newest audio"
        )

    result, index = _drive_sustained_episode(
        worker,
        tap_root,
        channel_id,
        first_index=index,
        per_scan=per_scan,
        final_files=final_files,
    )
    assert result.overloaded_channels == (channel_id,), (
        "a channel that spent its whole shed budget without holding the cadence did "
        "not trip the retained pause ladder"
    )
    return result, index


class _SlowRetentionPolicy:
    """Retention cleanup that BLOCKS, like the real one on a loaded host.

    Measured on the Blackwell box (2026-09-16): one enforce_discovered() took
    ~4.1-5.6 s because it SHA-256s every processed chunk and every evidence WAV
    (8,105 candidates).  That runs on the caption tap thread BEFORE the backlog
    gate, so segments accumulate while it blocks and the gate then fails closed
    on a backlog cleanup itself created.
    """

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.ready = True
        self.refusal_reason: str | None = None
        #: Signals that a sweep has ENTERED the blocking section, so a test can
        #: land the next segment deterministically instead of sleeping and hoping.
        self.entered = threading.Event()
        #: Optional explicit release: when set, a "slow" sweep waits on this event
        #: instead of a fixed delay, so a test can hold it blocked indefinitely and
        #: then release it deterministically (no flaky wall-clock waits).
        self.release: threading.Event | None = None

    def enforce_discovered(self, *, tap_root, review_store, segment_seconds):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.entered.set()
        if self.delay_seconds:
            if self.release is not None:
                self.release.wait(self.delay_seconds)
            else:
                threading.Event().wait(self.delay_seconds)
        return SimpleNamespace(ready=self.ready, refusal_reason=self.refusal_reason)


class _ScriptedRetentionPolicy:
    """Retention policy with scripted per-call outcomes for state testing.

    Each entry is one of: "ok" (ready), "refused" (ready=False with a reason),
    an Exception instance to raise, or ANOTHER policy OBJECT to delegate to.
    Lets a test drive the initial-refusal, first-failure, later-failure and
    recovery paths deterministically, and compose with a slow policy so an async
    sweep can be observed while it is genuinely in flight.
    """

    def __init__(self, outcomes) -> None:  # type: ignore[no-untyped-def]
        self.outcomes = list(outcomes)
        self.calls = 0

    def enforce_discovered(self, *, tap_root, review_store, segment_seconds):  # type: ignore[no-untyped-def]
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "refused":
            return SimpleNamespace(ready=False, refusal_reason="scripted-refusal")
        if outcome == "ok":
            return SimpleNamespace(ready=True, refusal_reason=None)
        # A nested policy object: delegate, so e.g. a SLOW policy actually blocks
        # and the sweep can be observed mid-flight.  Returning an "ok" namespace
        # here would silently swallow it (a real bug in an earlier version of this
        # double, which made a shutdown test pass without exercising anything).
        return outcome.enforce_discovered(
            tap_root=tap_root,
            review_store=review_store,
            segment_seconds=segment_seconds,
        )


class _VaryingLiveRuntime:
    """Live ASR shape: real speech, DIFFERENT words on every 9 s window.

    Mirrors the measured Blackwell behaviour (2026-09-16, device=cuda/float16):
    the historical tap fed 5 s segments with 4 s of overlap, so each 9 s window
    advanced 5 s and transcribed different words. The explicit override below
    retains that regression shape. Confirmation requires matching words in the
    shared audio interval, not merely overlapping geometry.
    """

    _TEXTS = (
        "Dewpoints in Arizona. We're in the 40s and 30s.",
        "We're in the 40s and 30s, so very much shuts down.",
        "Friday just really big drop in the amount of convection.",
        "Sunday a little more activity in the mountains.",
        "And back comes the moisture with thunderstorms.",
    )

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(
        self,
        chunks: Iterable[AudioChunk],
        vocabulary: CustomVocabulary | None = None,
    ) -> Iterable[CaptionHypothesis]:
        for chunk in chunks:
            text = self._TEXTS[self.calls % len(self._TEXTS)]
            self.calls += 1
            yield CaptionHypothesis(
                source_id=f"{chunk.chunk_id}-live",
                start_seconds=chunk.start_seconds,
                end_seconds=chunk.end_seconds,
                text=text,
                confidence=0.9,
            )


@pytest.mark.parametrize("publish_method", ["_publish_cues", "publish_for_current_session"])
def test_session_reset_serializes_with_an_already_validated_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publish_method: str
) -> None:
    worker = _worker(tmp_path / "tap", _ScriptedRuntime(), InMemoryCaptionReviewStore())
    worker._sweep_retention()
    worker.begin_channel_session("public")
    generation = worker._session_generation["public"]
    publisher = worker._publisher_for("public")
    entered, release, resetting, reset_done = (threading.Event() for _ in range(4))
    original_publish = publisher.publish

    def held_publish(cues):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(5)
        original_publish(cues)

    monkeypatch.setattr(publisher, "publish", held_publish)
    cue = CaptionCue(
        cue_id="old",
        start_seconds=0,
        end_seconds=1,
        text="STALE BROADCAST",
        confidence=0.9,
        low_confidence=False,
    )

    def reset() -> None:
        resetting.set()
        worker.begin_channel_session("public")
        reset_done.set()

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        publication = pool.submit(getattr(worker, publish_method), "public", generation, [cue])
        try:
            assert entered.wait(5)
            restart = pool.submit(reset)
            assert resetting.wait(5)
            # Allow the old implementation to finish its unprotected reset.
            reset_done.wait(0.1)
        finally:
            release.set()
        publication.result(5)
        restart.result(5)
    assert _active_vtt(tmp_path / "tap", "public").read_text().strip() == "WEBVTT"


@pytest.mark.parametrize("pause_at", ["read", "asr"])
def test_old_segment_cannot_enter_or_damage_a_restarted_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pause_at: str
) -> None:
    tap_root = tmp_path / "tap"
    channel = tap_root / "public"
    segment = channel / "chunk-000001.wav"
    runtime = _ScriptedRuntime("STALE BROADCAST")
    worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore(), atomic_segments=True)
    worker.begin_channel_session("public")
    _write_wav(segment)
    entered, release = threading.Event(), threading.Event()
    original_read = worker._read_chunk
    original_transcribe = runtime.transcribe

    def held_read(*args):  # type: ignore[no-untyped-def]
        chunk = original_read(*args)
        entered.set()
        assert release.wait(5)
        return chunk

    def held_transcribe(*args, **kwargs):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(5)
        yield from original_transcribe(*args, **kwargs)

    if pause_at == "read":
        monkeypatch.setattr(worker, "_read_chunk", held_read)
    else:
        monkeypatch.setattr(runtime, "transcribe", held_transcribe)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        scan = pool.submit(worker._process_channel, "public", channel, [(1, segment)])
        try:
            assert entered.wait(5)
            # Restart must finish without waiting for slow read/ASR.
            worker.begin_channel_session("public")
            _write_wav(segment, seconds=2)
            fresh_bytes = segment.read_bytes()
        finally:
            release.set()
        scan.result(5)
    assert segment.is_file(), "old scan moved the new session's same-numbered WAV"
    assert segment.read_bytes() == fresh_bytes
    assert "public" not in worker._previous_segments, "old overlap restored after reset"
    assert _active_vtt(tap_root, "public").read_text().strip() == "WEBVTT"
    if pause_at == "read":
        assert not runtime.seen_chunks, "audio read before reset entered new session ASR"


def test_queued_scan_keeps_its_session_across_runtime_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tap_root = tmp_path / "tap"
    segment = tap_root / "public" / "chunk-000001.wav"
    runtime = _ScriptedRuntime()
    worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore(), atomic_segments=True)
    worker.begin_channel_session("public")
    _write_wav(segment)

    def prepare() -> None:
        worker.begin_channel_session("public")
        _write_wav(segment, seconds=2)

    monkeypatch.setattr(runtime, "prepare", prepare, raising=False)
    result = worker.run_once()
    assert result.consumed_segments == 0
    assert not runtime.seen_chunks
    assert segment.is_file()
    assert "public" not in worker._channel_workers


def test_flush_keeps_the_worker_session_when_restart_occurs_during_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tap_root = tmp_path / "tap"
    worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())
    worker._sweep_retention()
    worker.begin_channel_session("public")
    live_worker = worker._worker_for("public")
    cue = CaptionCue(
        cue_id="old",
        start_seconds=0,
        end_seconds=1,
        text="STALE FLUSH",
        confidence=0.9,
        low_confidence=False,
    )

    def flush():  # type: ignore[no-untyped-def]
        worker.begin_channel_session("public")
        return SimpleNamespace(committed_review_items=[], expired_unconfirmed_cues=[])

    monkeypatch.setattr(live_worker, "flush", flush)
    monkeypatch.setattr(live_worker, "committed_cues", lambda: [cue])
    worker.flush_channel("public")
    assert _active_vtt(tap_root, "public").read_text().strip() == "WEBVTT"


class TestCaptionTapWorker:
    def test_refused_storage_drains_other_channels_when_sidecar_reset_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from civiccast.captions.live_sidecar import LiveWebVttPublisher

        tap_root = tmp_path / "tap"
        worker = self._retention_worker(
            tap_root, _ScriptedRetentionPolicy(["refused"]), _FakeClock()
        )
        for channel in ("alpha", "zulu"):
            _write_wav(tap_root / channel / "chunk-000001.wav")
        original_reset = LiveWebVttPublisher.reset

        def reset(publisher: LiveWebVttPublisher) -> None:
            if "alpha" in publisher.active_path.parts:
                raise OSError("sidecar directory unavailable")
            original_reset(publisher)

        monkeypatch.setattr(LiveWebVttPublisher, "reset", reset)
        worker.run_once()
        assert not list(tap_root.glob("*/*.wav"))
        assert not worker._review_store.list()

    @pytest.mark.parametrize("destination", ["collision", "quarantine"])
    def test_pending_recheck_does_not_retain_rejected_raw_audio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: str
    ) -> None:
        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        slow = _SlowRetentionPolicy(delay_seconds=10)
        slow.release = threading.Event()
        worker = self._retention_worker(tap_root, _ScriptedRetentionPolicy(["ok", slow]), clock)
        monkeypatch.setattr(
            worker._retention_policy, "record_event", lambda **_: None, raising=False
        )
        worker.run_once()
        segment = tap_root / "public" / "chunk-000001.wav"
        _write_wav(segment)
        if destination == "collision":
            _write_wav(segment.parent / "processed" / segment.name)
        else:
            segment.write_bytes(b"invalid wav")
        clock.advance(61)
        try:
            worker.run_once()
            assert worker._retention_in_flight
            assert not segment.exists()
            assert not list((segment.parent / destination).glob("*.wav"))
        finally:
            slow.release.set()
            assert worker.wait_for_retention_sweep(10)

    @pytest.mark.parametrize("publication", ["flush", "public-seam"])
    def test_refused_retention_cannot_republish_already_committed_cues(
        self, tmp_path: Path, publication: str
    ) -> None:
        tap_root = tmp_path / "tap"
        worker = self._retention_worker(
            tap_root, _ScriptedRetentionPolicy(["ok", "refused"]), _FakeClock()
        )
        for index in range(2):
            _write_wav(tap_root / "public" / f"chunk-{index:06d}.wav", seconds=5)
        worker.run_once()
        cues = worker._worker_for("public").committed_cues()
        assert cues
        worker._run_retention_sweep()
        worker._publisher_for("public").reset()
        if publication == "flush":
            worker.flush_channel("public")
        else:
            worker.publish_for_current_session(
                "public", worker._session_generation.get("public", 0), cues
            )
        assert _active_vtt(tap_root, "public").read_text().strip() == "WEBVTT"

    def test_failed_session_reset_stays_disabled_until_a_successful_reset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from civiccast.captions.live_sidecar import LiveWebVttPublisher

        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore(), atomic_segments=True)
        worker.begin_channel_session("public")
        for index in range(2):
            _write_wav(tap_root / "public" / f"chunk-{index:06d}.wav")

        def failed_reset(_self: LiveWebVttPublisher) -> None:
            raise OSError("disk reset denied")

        with monkeypatch.context() as patch:
            patch.setattr(LiveWebVttPublisher, "reset", failed_reset)
            with pytest.raises(OSError, match="disk reset denied"):
                worker.begin_channel_session("public")
        assert worker.run_once().consumed_segments == 0
        assert not runtime.seen_chunks
        worker.begin_channel_session("public")
        for index in range(2):
            _write_wav(tap_root / "public" / f"chunk-{index:06d}.wav")
        assert worker.run_once().consumed_segments == 2

    def test_pending_recheck_keeps_live_text_but_retains_no_audio(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        slow = _SlowRetentionPolicy(delay_seconds=10)
        slow.release = threading.Event()
        policy = _ScriptedRetentionPolicy(["ok", slow])
        worker = self._retention_worker(tap_root, policy, clock)
        worker.run_once()
        clock.advance(61)
        for index in range(2):
            _write_wav(tap_root / "public" / f"chunk-{index:06d}.wav", seconds=5)
        try:
            result = worker.run_once()
            assert slow.entered.wait(timeout=10.0)
            assert worker._retention_in_flight
            assert result.consumed_segments == 2
            assert not list((tap_root / "public" / "processed").glob("*.wav"))
            assert not list((tap_root.parent / "egress").rglob("evidence/*.wav"))
            items = worker._review_store.list()
            assert items, "text review must remain available during audio verification"
            assert all(not item.audio_evidence_available for item in items)
            assert _active_vtt(tap_root, "public").read_text().strip() != "WEBVTT"
        finally:
            slow.release.set()
            assert worker.wait_for_retention_sweep(10)

    def test_refusal_during_asr_cannot_persist_audio_or_publish(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tap_root = tmp_path / "tap"
        policy = _ScriptedRetentionPolicy(["ok", "refused"])
        worker = self._retention_worker(tap_root, policy, _FakeClock())
        worker.run_once()
        original = worker._runtime.transcribe

        def refuse_during_transcription(*args, **kwargs):  # type: ignore[no-untyped-def]
            worker._run_retention_sweep()
            yield from original(*args, **kwargs)

        monkeypatch.setattr(worker._runtime, "transcribe", refuse_during_transcription)
        for index in range(2):
            _write_wav(tap_root / "public" / f"chunk-{index:06d}.wav", seconds=5)
        worker.run_once()
        assert not worker._retention_ready
        assert not worker._review_store.list()
        assert not list((tap_root / "public" / "processed").glob("*.wav"))
        assert not list((tap_root.parent / "egress").rglob("evidence/*.wav"))
        assert _active_vtt(tap_root, "public").read_text().strip() == "WEBVTT"

    def test_run_once_idles_cleanly_when_the_tap_directory_does_not_exist(
        self,
        tmp_path: Path,
    ) -> None:
        """Item 91: an off-switched station never even builds a
        ``CaptionTapWorker`` (``civiccast.app`` gates that on
        ``mode == "inline"``), but this covers the adjacent, already-shipped
        guarantee that keeps the switch safe either way -- a scan against a
        tap root that has not been created yet (or was created, then
        removed, e.g. by an operator cleaning up after switching off) must
        return an empty result, never raise ``OSError``/``FileNotFoundError``
        from ``Path.iterdir()``."""

        tap_root = tmp_path / "tap-not-created"
        assert not tap_root.exists()
        worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())

        result = worker.run_once()

        assert result.consumed_segments == 0
        assert result.channels == ()

    def test_atomic_gstreamer_segment_is_consumed_without_waiting_for_a_newer_file(
        self,
        tmp_path: Path,
    ) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        # Written AFTER construction: audio that already exists when the worker
        # starts belongs to a previous session/process and is discarded at
        # startup (U11 B1).  In production the tap keeps writing while the worker
        # runs, which is the ordering every scan-shaped test now uses.
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            atomic_segments=True,
        )
        _write_wav(tap_root / "gov-ch12" / "chunk-000000.wav")

        result = worker.run_once()

        assert result.consumed_segments == 1
        assert len(runtime.seen_chunks) == 1
        assert (tap_root / "gov-ch12" / "processed" / "chunk-000000.wav").is_file()

    def test_default_five_second_segments_form_overlapping_stable_windows(
        self,
        tmp_path: Path,
    ) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        store = InMemoryCaptionReviewStore()
        worker = _worker(
            tap_root,
            runtime,
            store,
            segment_seconds=5.0,
        )
        for index in range(3):
            _write_wav(
                tap_root / "gov-ch12" / f"chunk-{index:06d}.wav",
                seconds=5.0,
            )

        result = worker.run_once()

        assert result.consumed_segments == 2
        assert len(runtime.seen_chunks) == 2
        assert runtime.seen_chunks[0].start_seconds == 0.0
        assert runtime.seen_chunks[1].start_seconds == 0.0
        assert runtime.seen_chunks[1].end_seconds == 10.0
        assert len(store.list()) == 1
        assert _active_vtt(tap_root, "gov-ch12").is_file()

    @pytest.mark.parametrize("source", ["constructor", "settings", "environment-override"])
    def test_overlap_replays_full_previous_default_segment_unless_overridden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP_OVERLAP_SECONDS", raising=False)
        if source == "environment-override":
            monkeypatch.setenv("CIVICCAST_CAPTION_TAP_OVERLAP_SECONDS", "4")
        options = (
            {}
            if source == "constructor"
            else {"overlap_seconds": CaptionTapWorkerSettings.from_env().overlap_seconds}
        )
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tmp_path / "tap",
            caption_work_dir=tmp_path / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            **options,
        )
        # Distinct first-second samples expose loss of the old default's omitted
        # second. Check actual PCM, not only the window's declared timestamps.
        second = TAP_SAMPLE_RATE_HZ * 2
        previous_pcm = b"\x01\x00" * TAP_SAMPLE_RATE_HZ + b"\x02\x00" * (TAP_SAMPLE_RATE_HZ * 4)
        current_pcm = b"\x03\x00" * (TAP_SAMPLE_RATE_HZ * 5)
        previous = AudioChunk(
            chunk_id="previous",
            start_seconds=0.0,
            end_seconds=5.0,
            sample_rate_hz=TAP_SAMPLE_RATE_HZ,
            pcm_s16le=previous_pcm,
        )
        current = AudioChunk(
            chunk_id="current",
            start_seconds=5.0,
            end_seconds=10.0,
            sample_rate_hz=TAP_SAMPLE_RATE_HZ,
            pcm_s16le=current_pcm,
        )
        worker._previous_segments["public"] = (0, previous)

        window = worker._with_overlap("public", 1, current)

        omitted_seconds = 1 if source == "environment-override" else 0
        assert window.start_seconds == float(omitted_seconds)
        assert window.end_seconds == 10.0
        assert len(window.pcm_s16le) == second * (10 - omitted_seconds)
        assert window.pcm_s16le == previous_pcm[second * omitted_seconds :] + current_pcm

    def test_settled_segments_become_durable_review_items(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        store = InMemoryCaptionReviewStore()
        worker = _worker(tap_root, runtime, store)
        # Two settled segments (a newer one exists after each) + the newest,
        # possibly still being written by ffmpeg, which must NOT be consumed.
        _write_wav(tap_root / "gov-ch12" / "chunk-000000.wav")
        _write_wav(tap_root / "gov-ch12" / "chunk-000001.wav")
        _write_wav(tap_root / "gov-ch12" / "chunk-000002.wav")

        result = worker.run_once()

        assert result.consumed_segments == 2
        assert len(runtime.seen_chunks) == 2
        assert runtime.seen_chunks[0].sample_rate_hz == TAP_SAMPLE_RATE_HZ
        # Chunk timing comes from the segment index (segment_seconds=1.0).
        assert runtime.seen_chunks[0].start_seconds == 0.0
        assert runtime.seen_chunks[1].start_seconds == 0.0
        items = store.list()
        assert items, "stable cues must land in the durable review queue"
        assert all(item.asset_id == "gov-ch12" for item in items)
        assert items[0].audio_evidence_available is True
        sidecar = _active_vtt(tap_root, "gov-ch12")
        assert sidecar.is_file()
        cues = load_caption_cues_from_timed_text(sidecar, source_id="gov-ch12")
        assert [(cue.text, cue.start_seconds, cue.end_seconds) for cue in cues] == [
            ("the council will come to order", 0.0, 1.0)
        ]

    def test_active_sidecar_is_the_caption_feed_input(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        store = InMemoryCaptionReviewStore()
        tap_worker = _worker(tap_root, runtime, store)
        for index in range(3):
            _write_wav(tap_root / "gov-ch12" / f"chunk-{index:06d}.wav")

        tap_worker.run_once()

        sent: list[dict[str, object]] = []
        feed_worker = CaptionFeedWorker(
            work_dir=tap_root.parent / "egress",
            on_air_channels=lambda: ["gov-ch12"],
            caption_cue_provider=lambda channel_id: load_caption_cues_from_timed_text(
                _active_vtt(tap_root, channel_id),
                source_id=channel_id,
            ),
            send_caption_cue=lambda channel_id, _work_dir, **cue: (
                sent.append({"channel_id": channel_id, **cue}) or True
            ),
        )

        result = feed_worker.run_once()

        assert result.cues_sent == 1
        delivery_id = sent[0].pop("delivery_id")
        assert str(uuid.UUID(str(delivery_id))) == delivery_id
        assert sent == [
            {
                "channel_id": "gov-ch12",
                "text": "the council will come to order",
                "pts_seconds": 0.0,
                "duration_seconds": 1.0,
            }
        ]

    def test_consumed_segments_are_not_reprocessed(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        store = InMemoryCaptionReviewStore()
        worker = _worker(tap_root, runtime, store)
        _write_wav(tap_root / "gov-ch12" / "chunk-000000.wav")
        _write_wav(tap_root / "gov-ch12" / "chunk-000001.wav")

        first = worker.run_once()
        second = worker.run_once()

        assert first.consumed_segments == 1
        assert second.consumed_segments == 0
        assert len(runtime.seen_chunks) == 1

    def test_varying_live_asr_text_still_reaches_the_active_sidecar(
        self,
        tmp_path: Path,
    ) -> None:
        """Real live speech must reach active.vtt even though every window differs.

        Measured on the Blackwell GPU run (2026-09-16, device=cuda/float16): the
        caption tap processed 960+ chunks and transcribed real speech correctly,
        yet active.vtt stayed at 7 bytes (empty WebVTT) and the emitted stream
        carried only A/53 null padding.  Cause: the tap feeds 5 s segments with
        4 s overlap (9 s windows advancing 5 s) while the stabilizer required the
        SAME normalized text twice, which continuous speech never produces.  The
        live tap therefore corroborates only matching words in shared audio.
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        runtime = _VaryingLiveRuntime()
        store = InMemoryCaptionReviewStore()
        # Historical production geometry, kept as an explicit override: 5 s
        # segments with 4 s overlap -> 9 s windows advancing 5 s. The current
        # default overlaps the full previous 5 s segment. (The harness default
        # of a 1 s segment collapses both windows onto start 0.0, not the live
        # shape this regression is about.)
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=runtime,
            review_store=store,
            segment_seconds=5.0,
            overlap_seconds=4.0,
            atomic_segments=True,
        )
        # Two 5 s chunks in the first scan: within max_backlog=2, and enough for
        # one window to be corroborated by the next overlapping window.
        for index in range(2):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        result = worker.run_once()

        assert result.consumed_segments >= 2
        sidecar = _active_vtt(tap_root, channel)
        assert sidecar.is_file()
        cues = load_caption_cues_from_timed_text(sidecar, source_id=channel)
        assert cues, (
            "live windows produced no active cue; the emitted stream would carry no caption text"
        )
        # The corroborated (later, overlapping) window supplies the committed text.
        assert any("40s and 30s" in cue.text for cue in cues)

    def test_stale_session_publish_cannot_restore_the_previous_broadcast(
        self, tmp_path: Path
    ) -> None:
        """A cue published by the PREVIOUS session must not overwrite the reset.

        Regression for the reset race: begin_channel_session() blanked the
        sidecar, but a caption result still in flight from the old session's
        worker could publish afterwards and put the old broadcast's cue back on
        air (reproduced directly: reset -> old publish -> old caption visible).
        The publish path is now generation-stamped, so a result carrying a
        generation older than the channel's current one is dropped.
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        (tap_root / channel).mkdir(parents=True)
        store = InMemoryCaptionReviewStore()
        worker = _worker(tap_root, _ScriptedRuntime(), store)

        # Seed the sidecar the way a previous broadcast left it.
        publisher = worker._publisher_for(channel)
        publisher.publish(
            [
                CaptionCue(
                    cue_id="cue-OLD",
                    start_seconds=10.0,
                    end_seconds=18.0,
                    text="OLD SESSION CAPTION",
                    confidence=0.9,
                    low_confidence=False,
                )
            ]
        )
        sidecar = _active_vtt(tap_root, channel)
        assert "OLD SESSION CAPTION" in sidecar.read_text(encoding="utf-8")

        # Capture the OLD generation, then start a new session.
        stale_generation = worker._session_generation.get(channel, 0)
        worker.begin_channel_session(channel)
        assert "OLD SESSION CAPTION" not in sidecar.read_text(encoding="utf-8")

        # A late publish from the old session must be refused.  Exercise the
        # public publish path rather than a helper, so the assertion is
        # behavioural and would fail on unfixed code for the right reason.
        stale_cues = [
            CaptionCue(
                cue_id="cue-OLD",
                start_seconds=10.0,
                end_seconds=18.0,
                text="OLD SESSION CAPTION",
                confidence=0.9,
                low_confidence=False,
            )
        ]
        worker.publish_for_current_session(channel, stale_generation, stale_cues)

        remaining = sidecar.read_text(encoding="utf-8")
        assert "OLD SESSION CAPTION" not in remaining, (
            "a finished session's caption reappeared after the reset"
        )

    def test_retention_cleanup_latency_does_not_cause_a_false_overload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A MID-SESSION cleanup sweep must not manufacture a false overload.

        Isolates the measured field defect (Blackwell 2026-09-16, 89 s after
        ON_AIR) without touching the max-2 contract:

          - max_backlog_segments stays 2; a backlog of 2 at gate time is legal
          - the FIRST sweep is synchronous by design (it establishes the retention
            verdict before any ASR -- the original safety guarantee), so the
            dangerous sweep is a LATER one, on the 60 s cadence, with captions
            already live.  The defect was that a later sweep also ran inline and
            blocked ~5 s, so a third segment arrived during cleanup and tipped a
            legal backlog over the line.

        Models that: sweep once (synchronous, establishing the verdict), then make
        the NEXT sweep slow and land a third segment while it would be running.
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        (tap_root / channel).mkdir(parents=True)
        store = InMemoryCaptionReviewStore()
        slow = _SlowRetentionPolicy()
        clock = _FakeClock()

        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=store,
            segment_seconds=5.0,
            atomic_segments=True,
            retention_policy=slow,
            monotonic=clock,
        )

        # First scan: the verdict is established before any ASR (whatever the
        # implementation, this must hold).  Assert the OBSERVABLE consequence --
        # one sweep ran and the store was accepted -- not a new internal flag, so
        # this regression stays behavioural and can run against older code.
        _write_wav(tap_root / channel / "chunk-000000.wav", seconds=5.0)
        worker.run_once()
        assert slow.calls == 1, "the first scan must verify retention before ASR"

        # A live cue is on air now.
        worker._publisher_for(channel).publish(
            [
                CaptionCue(
                    cue_id="cue-LIVE",
                    start_seconds=0.0,
                    end_seconds=4.0,
                    text="LIVE CAPTION ON AIR",
                    confidence=0.9,
                    low_confidence=False,
                )
            ]
        )
        sidecar = _active_vtt(tap_root, channel)

        # Advance past the sweep cadence so the NEXT scan re-sweeps -- and that
        # sweep is slow.  A legal 2-segment backlog exists when it starts.
        clock.advance(61.0)
        _write_wav(tap_root / channel / "chunk-000001.wav", seconds=5.0)
        _write_wav(tap_root / channel / "chunk-000002.wav", seconds=5.0)

        # Pin the legal backlog snapshot BEFORE the third arrival. Waiting only
        # for sweep-entered races the actual backlog gate: instrumented CI can
        # legitimately see all three and correctly overload. That was a test
        # scheduling bug, not permission to weaken the max-two gate.
        gate_sampled, third_landed = threading.Event(), threading.Event()
        original_settled = worker._settled_segments

        def settled(channel_dir: Path) -> list[tuple[int, Path]]:
            snapshot = original_settled(channel_dir)
            gate_sampled.set()
            assert third_landed.wait(10)
            return snapshot

        monkeypatch.setattr(worker, "_settled_segments", settled)

        def _land_third() -> None:
            if gate_sampled.wait(timeout=10.0):
                _write_wav(tap_root / channel / "chunk-000003.wav", seconds=5.0)
                third_landed.set()

        slow.entered.clear()
        slow.delay_seconds = 30
        slow.release = threading.Event()
        lander = threading.Thread(target=_land_third, daemon=True)
        lander.start()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            scan = pool.submit(worker.run_once)
            try:
                assert slow.entered.wait(10)
                # Must complete while retention is STILL blocked. A synchronous
                # sweep cannot pass by simply being faster on this machine.
                result = scan.result(timeout=10)
                assert worker._retention_in_flight
                assert third_landed.is_set()
            finally:
                slow.release.set()
                third_landed.set()
                gate_sampled.set()
                lander.join(timeout=10.0)
                assert worker.wait_for_retention_sweep(timeout=15.0)

        assert result.dropped_overload_segments == 0, (
            "a mid-session cleanup sweep manufactured a fail-closed overload"
        )
        assert "public" not in result.overloaded_channels
        assert "public" not in result.paused_channels
        text = sidecar.read_text(encoding="utf-8")
        assert text.strip() != "WEBVTT", "a mid-session cleanup sweep cleared live captions"

    def test_real_overload_still_fails_closed_when_cleanup_is_off_path(
        self, tmp_path: Path
    ) -> None:
        """The 120s fail-closed must survive the cleanup fix, unchanged.

        Companion to the cleanup-latency regression: moving retention off the
        caption thread must NOT weaken the operator's protection.  A backlog that
        stays over ``max_backlog_segments`` for the whole persistence window
        still pauses, clears, and discards -- exactly as before, with a fast
        (non-blocking) retention policy.
        """

        tap_root = tmp_path / "tap"
        channel = "government"
        (tap_root / channel).mkdir(parents=True)
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            retention_policy=_SlowRetentionPolicy(delay_seconds=0.0),
            # Catch-up OFF (the documented U23 escape hatch): this test is about
            # the fail-closed pause path itself surviving the cleanup fix, so it
            # must exercise that path on the very first persistent overshoot
            # rather than shedding first. Catch-up's own behaviour is covered by
            # ``TestCaptionTapCatchUp``.
            catch_up_shed_limit=0,
        )
        active = _active_vtt(tap_root, channel)
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text(
            "WEBVTT\n\nold\n00:00:00.000 --> 00:00:01.000\nstale caption\n",
            encoding="utf-8",
        )
        # A genuine overload: over the threshold on every scan of the window.
        # (One overshoot alone is spared by the persistence rule; see
        # ``_drive_to_the_edge_of_the_overload_window``.)
        next_index = _drive_to_the_edge_of_the_overload_window(
            worker, tap_root, channel, per_scan=4
        )
        for index in range(next_index, next_index + 4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        result = worker.run_once()

        assert result.consumed_segments == 0
        assert "government" in result.overloaded_channels
        assert "government" in result.paused_channels
        assert result.dropped_overload_segments > 0
        assert load_caption_cues_from_timed_text(active, source_id="government") == []
        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["state"] == "paused"
        assert status["resume_in_seconds"] > 0

    def _retention_worker(self, tap_root: Path, policy: object, clock: object):
        return CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            retention_policy=policy,  # type: ignore[arg-type]
            monotonic=clock,  # type: ignore[arg-type]
        )

    def test_initial_retention_refusal_blocks_asr_before_any_verdict(self, tmp_path: Path) -> None:
        """An UNVERIFIED/refusing store must not be transcribed into.

        The sweep used to be synchronous, so a verdict existed before ASR.  Now
        that later sweeps are async, the initial state must still fail closed: a
        first sweep that refuses must stop ASR entirely and report
        ``storage-refused``, never silently transcribe.
        """

        tap_root = tmp_path / "tap"
        (tap_root / "public").mkdir(parents=True)
        _write_wav(tap_root / "public" / "chunk-000000.wav", seconds=5.0)
        worker = self._retention_worker(
            tap_root, _ScriptedRetentionPolicy(["refused"]), _FakeClock()
        )

        result = worker.run_once()

        assert result.consumed_segments == 0
        assert worker._retention_ready is False
        assert worker._retention_refusal == "scripted-refusal"

    def test_failed_first_sweep_fails_closed_not_open(self, tmp_path: Path) -> None:
        """A sweep that RAISES must leave the store unverified, not 'ready'.

        The dangerous default would be to treat an exception as permission to
        transcribe.  A store we could not verify is not a store we may write
        caption evidence into.
        """

        tap_root = tmp_path / "tap"
        (tap_root / "public").mkdir(parents=True)
        _write_wav(tap_root / "public" / "chunk-000000.wav", seconds=5.0)
        worker = self._retention_worker(
            tap_root, _ScriptedRetentionPolicy([RuntimeError("sweep boom")]), _FakeClock()
        )

        result = worker.run_once()

        assert result.consumed_segments == 0
        assert worker._retention_ready is False
        assert worker._retention_refusal == "retention-verification-failed"

    def test_later_sweep_failure_also_fails_closed_then_recovers(self, tmp_path: Path) -> None:
        """After a SUCCESSFUL sweep, a later failure must still fail closed.

        Accuracy check on the exception policy: an earlier implementation only
        failed closed when ``not _retention_verified``, so a post-success failure
        left ready=True and an unverifiable store kept being transcribed into.
        The intended policy is fail-closed on ANY sweep failure, with explicit
        recovery on the next SUCCESSFUL sweep (a fail-closed-with-retry, not a
        latch).
        """

        tap_root = tmp_path / "tap"
        (tap_root / "public").mkdir(parents=True)
        clock = _FakeClock()
        policy = _ScriptedRetentionPolicy(["ok", RuntimeError("later boom"), "ok"])
        worker = self._retention_worker(tap_root, policy, clock)

        # Sweep 1 succeeds -> verified + ready.
        _write_wav(tap_root / "public" / "chunk-000000.wav", seconds=5.0)
        worker.run_once()
        assert policy.calls == 1
        assert worker._retention_ready is True

        # Sweep 2 (later) fails -> must FAIL CLOSED even though it was verified.
        clock.advance(61.0)
        _write_wav(tap_root / "public" / "chunk-000001.wav", seconds=5.0)
        worker.run_once()
        assert worker.wait_for_retention_sweep(timeout=10.0)
        assert policy.calls == 2
        assert worker._retention_ready is False, (
            "a later sweep failure kept ready=True; an unverifiable store would "
            "keep being transcribed into"
        )
        assert worker._retention_refusal == "retention-verification-failed"

        # Sweep 3 succeeds again -> recovery (not a latch).
        clock.advance(61.0)
        _write_wav(tap_root / "public" / "chunk-000002.wav", seconds=5.0)
        worker.run_once()
        assert worker.wait_for_retention_sweep(timeout=10.0)
        assert worker._retention_ready is True, "a successful sweep must clear the failure"

    def test_run_forever_waits_for_the_retention_sweep_before_returning(
        self, tmp_path: Path
    ) -> None:
        """run_forever must itself wait for an in-flight sweep before returning.

        A soft shutdown (worker stopped/replaced while the process survives) must
        not return while a sweep is still running against dirs a successor worker
        may also sweep.

        The discriminator is measured AT LOOP RETURN with no further joining: the
        sweep is deliberately long enough (2 s) that it cannot finish during the
        loop's own poll wait, so it is only complete on return if run_forever
        waited for it.  Joining it in the test afterwards would mask the bug --
        that is exactly what an earlier version of this test did.
        """

        tap_root = tmp_path / "tap"
        (tap_root / "public").mkdir(parents=True)
        clock = _FakeClock()
        slow = _SlowRetentionPolicy(delay_seconds=2.0)
        policy = _ScriptedRetentionPolicy(["ok", slow])
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            retention_policy=policy,  # type: ignore[arg-type]
            monotonic=clock,
        )
        _write_wav(tap_root / "public" / "chunk-000000.wav", seconds=5.0)
        worker.run_once()  # synchronous first verification

        stop = threading.Event()
        clock.advance(61.0)  # next scan re-sweeps
        loop = threading.Thread(
            target=worker.run_forever,
            kwargs={"poll_seconds": 0.05, "stop_event": stop},
            daemon=True,
        )
        loop.start()
        # Wait until the slow async sweep has actually STARTED.
        assert slow.entered.wait(timeout=10.0), "async sweep never started"
        stop.set()
        loop.join(timeout=20.0)
        assert not loop.is_alive(), "run_forever did not exit on stop_event"

        # THE assertion: at loop return, with no further joining by the test, the
        # sweep must already be finished.
        thread = worker._retention_thread
        assert thread is not None
        assert not thread.is_alive(), (
            "run_forever returned while the retention sweep was still running; a "
            "successor worker could overlap it"
        )

    def test_successor_cannot_overlap_an_orphaned_sweep(self, tmp_path: Path) -> None:
        """No overlapping sweep, and sweeps resume once an orphan completes.

        Reachable path (traced): ThreadSupervisor.start() can be called again after
        stop(), so the same worker can be re-entered while a sweep from the
        previous loop is still running.

        MEASURED OUTCOME: overlap does not occur.  ``_retention_in_flight`` stays
        True until the running sweep finishes and the dispatch path returns early
        on that flag, so the successor does not dispatch.  Verified by deleting the
        shutdown branch entirely and re-running this scenario: the successor still
        did not dispatch.  This test therefore pins the INVARIANT; it is not a RED
        for any guard, and no extra production state exists for it.
        """

        tap_root = tmp_path / "tap"
        (tap_root / "public").mkdir(parents=True)
        clock = _FakeClock()

        blocking = _SlowRetentionPolicy(delay_seconds=30.0)  # far beyond the wait
        blocking.release = threading.Event()
        policy = _ScriptedRetentionPolicy(["ok", blocking])
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            retention_policy=policy,  # type: ignore[arg-type]
            monotonic=clock,
        )
        _write_wav(tap_root / "public" / "chunk-000000.wav", seconds=5.0)
        worker.run_once()  # synchronous first verification
        worker._retention_shutdown_timeout = 0.2  # keep the test quick

        stop = threading.Event()
        clock.advance(61.0)
        loop = threading.Thread(
            target=worker.run_forever,
            kwargs={"poll_seconds": 0.05, "stop_event": stop},
            daemon=True,
        )
        loop.start()
        assert blocking.entered.wait(timeout=10.0), "async sweep never started"

        # Shutdown times out; run_forever returns with the sweep still alive.
        stop.set()
        loop.join(timeout=10.0)
        assert not loop.is_alive(), "run_forever did not return on stop_event"
        sweep = worker._retention_thread
        assert sweep is not None and sweep.is_alive(), "precondition: orphan is alive"

        # INVARIANT: a successor must NOT dispatch a second, overlapping sweep.
        calls_before = policy.calls
        clock.advance(61.0)
        _write_wav(tap_root / "public" / "chunk-000001.wav", seconds=5.0)
        worker.run_once()
        assert policy.calls == calls_before, (
            "a successor sweep was dispatched while the previous sweep was still "
            "running; two sweeps would overlap on the same store and directories"
        )

        # Release the blocker: the orphan finishes and sweeps resume.
        blocking.release.set()
        assert worker.wait_for_retention_sweep(timeout=15.0)
        clock.advance(61.0)
        _write_wav(tap_root / "public" / "chunk-000002.wav", seconds=5.0)
        worker.run_once()
        assert worker.wait_for_retention_sweep(timeout=15.0)
        assert policy.calls > calls_before, "sweeps did not resume after the orphan finished"

    def test_new_session_discards_leftover_segments_instead_of_overloading(
        self, tmp_path: Path
    ) -> None:
        """Leftover audio from a finished session must not cause a start-up overload.

        Found in the accept5 run: PID 10588 started 04:21:47 and logged a caption
        overload 28 s later at 04:22:15 while the public channel was on
        FALLBACK_SLATE (not airing the program).  The tap had picked up 4 settled
        segments left in the tap directory by the PREVIOUS broadcast, counted them
        as backlog, and paused captions for 120 s before any new audio existed.

        Those segments belong to a finished session and can never air, so a new
        session must discard them rather than treat them as work.
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        (tap_root / channel).mkdir(parents=True)
        worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())

        # Leftover settled audio from a previous broadcast.
        for index in range(4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        worker.begin_channel_session(channel)

        remaining = sorted(p.name for p in (tap_root / channel).glob("chunk-*.wav"))
        assert remaining == [], (
            f"leftover segments from the previous session survived the reset: {remaining}"
        )

        result = worker.run_once()
        assert result.dropped_overload_segments == 0
        assert "public" not in result.overloaded_channels, (
            "leftover segments from a finished session caused a start-up overload"
        )

    def test_resolved_identity_prefers_backend_and_reports_unavailable(self) -> None:
        """Loaded identity must come from the BACKEND, never from the request.

        Two guards:
          (a) an "auto" request whose backend loaded on CUDA must report the
              backend's device as loaded_device (not "auto"), with
              identity_source="backend-model";
          (b) with NO backend evidence, loaded_device must be "unavailable" -
              NEVER an echo of the requested value, which could otherwise print
              loaded_device=cuda with no loaded evidence.
        """

        from civiccast.captions.tap_worker import _resolved_runtime_identity

        class _Backend:
            device = "cuda"
            compute_type = "float16"

        class _Model:
            model = _Backend()

        class _AutoRuntime:
            device = "auto"
            compute_type = "float16"
            num_workers = 3
            _model = _Model()

            def on_cuda(self) -> bool:
                return True

        ident = _resolved_runtime_identity(_AutoRuntime())
        assert ident[0] == "auto"  # requested stays as requested
        assert ident[2] == "cuda"  # loaded comes from the backend
        assert ident[3] == "float16"
        assert ident[6] == "backend-model"

        class _NoBackendRuntime:
            device = "cuda"
            compute_type = "float16"
            num_workers = 3
            _model = None

            def on_cuda(self) -> bool:
                return True

        ident2 = _resolved_runtime_identity(_NoBackendRuntime())
        assert ident2[2] == "unavailable", "loaded_device echoed the request"
        assert ident2[3] == "unavailable"
        assert ident2[6] == "unavailable"

    def test_multiple_channels_keep_separate_caption_streams(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        store = InMemoryCaptionReviewStore()
        worker = _worker(tap_root, runtime, store)
        for channel in ("gov-ch12", "edu-ch20"):
            _write_wav(tap_root / channel / "chunk-000000.wav")
            _write_wav(tap_root / channel / "chunk-000001.wav")

        worker.run_once()
        # Settle the second segment of each channel and keep going.
        for channel in ("gov-ch12", "edu-ch20"):
            _write_wav(tap_root / channel / "chunk-000002.wav")
        worker.run_once()

        asset_ids = {item.asset_id for item in store.list()}
        assert asset_ids == {"gov-ch12", "edu-ch20"}
        for channel in asset_ids:
            cues = load_caption_cues_from_timed_text(
                _active_vtt(tap_root, channel),
                source_id=channel,
            )
            assert [cue.text for cue in cues] == ["the council will come to order"]

    def test_channels_are_transcribed_concurrently(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ConcurrencyProbeRuntime()
        # Explicit: the CPU/default-unknown bound is a flat 1, station-wide,
        # while the CUDA default is runtime-selected (see the neighboring
        # tests). This test is
        # about the executor actually running channels in parallel when the
        # bound allows it, so it states the bound it is testing rather than
        # relying on the default.
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            max_channel_workers=3,
        )
        for channel in ("public", "education", "government"):
            _write_wav(tap_root / channel / "chunk-000000.wav")
            _write_wav(tap_root / channel / "chunk-000001.wav")

        result = worker.run_once()

        assert result.consumed_segments == 3
        assert runtime.max_active == 3

    def test_cuda_default_keeps_three_channel_segment_cycle_in_flight(
        self,
        tmp_path: Path,
    ) -> None:
        """The Blackwell failure mode: three channels cannot queue behind one.

        Each channel produces a segment on the same five-second cadence.  The
        runtime measured one GPU transcription at 2.75 seconds, so serializing
        all three necessarily takes about 8.25 seconds and grows backlog.  The
        CUDA default must submit all three channel calls together; this probe
        blocks briefly inside each call, so ``max_active == 3`` proves the
        capacity choice reaches the executor rather than merely changing a
        constant that the product never uses.
        """

        tap_root = tmp_path / "tap"
        runtime = _CudaConcurrencyProbeRuntime()
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
        )
        for channel in ("public", "education", "government"):
            _write_wav(tap_root / channel / "chunk-000000.wav")
            _write_wav(tap_root / channel / "chunk-000001.wav")

        result = worker.run_once()

        assert result.consumed_segments == 3
        assert result.overloaded_channels == ()
        assert runtime.max_active == 3

    def test_first_three_channel_scan_resolves_cuda_fallback_before_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The first real scan never submits three calls onto fallback CPU."""

        for name in (
            "CIVICCAST_NATIVE_STATION",
            "CIVICCAST_WHISPER_MODEL_PATH",
            "CIVICCAST_WHISPER_DEVICE",
            "CIVICCAST_WHISPER_COMPUTE_TYPE",
            "CIVICCAST_WHISPER_NUM_WORKERS",
            "CIVICCAST_CAPTION_TAP_MAX_CHANNEL_WORKERS",
        ):
            monkeypatch.delenv(name, raising=False)

        attempts: list[str] = []
        active_cpu_calls = 0
        max_active_cpu_calls = 0
        active_lock = threading.Lock()
        preparation_priority_lowered = False

        def _record_lowered_priority() -> None:
            nonlocal preparation_priority_lowered
            preparation_priority_lowered = True

        monkeypatch.setattr(
            "civiccast.captions.tap_worker._lower_current_thread_priority",
            _record_lowered_priority,
        )

        class _FallbackModel:
            def __init__(self, _model: str, **kwargs: object) -> None:
                assert preparation_priority_lowered is True
                device = str(kwargs["device"])
                attempts.append(device)
                if device == "cuda":
                    raise RuntimeError("simulated CUDA loader failure")

            def transcribe(self, _path: str, **_kwargs: object) -> tuple[list[object], object]:
                nonlocal active_cpu_calls, max_active_cpu_calls
                with active_lock:
                    active_cpu_calls += 1
                    max_active_cpu_calls = max(max_active_cpu_calls, active_cpu_calls)
                time.sleep(0.05)
                with active_lock:
                    active_cpu_calls -= 1
                return [], object()

        monkeypatch.setattr(
            caption_runtime_module,
            "_load_whisper_model_class",
            lambda: _FallbackModel,
        )
        runtime = FasterWhisperRuntime(
            model_size_or_path="tiny",
            live=True,
            device="cuda",
            compute_type="float16",
        )
        tap_root = tmp_path / "tap"
        worker = build_tap_worker(
            CaptionTapWorkerSettings(mode="inline", tap_root=tap_root),
            InMemoryCaptionReviewStore(),
            runtime=runtime,
            caption_work_dir=tmp_path / "egress",
        )

        assert worker._max_channel_workers == 3

        # Written AFTER construction: audio that already exists when the worker
        # starts belongs to a previous session and is discarded at startup
        # (U11 B1).  This test is about the first DISPATCHED scan, so its audio
        # has to arrive the way live audio does -- while the worker is running.
        for channel in ("public", "education", "government"):
            _write_wav(tap_root / channel / "chunk-000000.wav")
            _write_wav(tap_root / channel / "chunk-000001.wav")

        result = worker.run_once()

        assert attempts == ["cuda", "cpu"]
        assert runtime.num_workers == 1
        assert worker._max_channel_workers == 1
        assert max_active_cpu_calls == 1
        assert result.consumed_segments == 3

    def test_asr_concurrency_is_bounded_by_cpu_count(self, tmp_path: Path) -> None:
        """The whole point of the bound: three ON_AIR channels, one at a time.

        MEASURED field failure this guards (tester DESKTOP-VBMA6O5, three
        channels ON_AIR, CPU-only): three channels transcribing at once, each
        faster-whisper model built with ``cpu_threads=0`` (every core), the
        control plane at ~247% of a core, and the playout workers restarted by
        their own 10-second stall watchdog.
        """

        tap_root = tmp_path / "tap"
        runtime = _ConcurrencyProbeRuntime()
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            max_channel_workers=1,
        )
        for channel in ("public", "education", "government"):
            _write_wav(tap_root / channel / "chunk-000000.wav")
            _write_wav(tap_root / channel / "chunk-000001.wav")

        result = worker.run_once()

        # Bounded, not dropped: every channel is still transcribed in this
        # same scan, just never simultaneously.
        assert result.consumed_segments == 3
        assert sorted(result.channels) == ["education", "government", "public"]
        assert runtime.max_active == 1

    def test_default_cpu_concurrency_is_always_one_channel_station_wide(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Item 79: the CPU default is a flat 1, regardless of core count.

        Tightened from the previous "one channel per 8 CPUs, max 3" formula.
        Read this as knob hardening, not as a standalone fix for the original
        DESKTOP-VBMA6O5 field defect (see the module docstring): every
        channel already shares one speech recognition model instance built
        with CTranslate2's ``inter_threads=1``, so the previous default of 3
        never actually ran 3 concurrent inferences -- that model-level queue
        was already serializing them.
        """

        monkeypatch.setattr("civiccast.captions.tap_worker.os.cpu_count", lambda: 4)
        assert default_max_channel_workers() == 1
        monkeypatch.setattr("civiccast.captions.tap_worker.os.cpu_count", lambda: 8)
        assert default_max_channel_workers() == 1
        monkeypatch.setattr("civiccast.captions.tap_worker.os.cpu_count", lambda: 32)
        assert default_max_channel_workers() == 1
        # `os.cpu_count()` is documented as possibly None; still 1.
        monkeypatch.setattr("civiccast.captions.tap_worker.os.cpu_count", lambda: None)
        assert default_max_channel_workers() == 1

    def test_cuda_concurrency_is_capped_by_runtime_workers(self) -> None:
        runtime = SimpleNamespace(on_cuda=lambda: True, num_workers=2)

        assert default_max_channel_workers(runtime) == 2

    def test_default_concurrency_env_override_still_works(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The documented ``CIVICCAST_CAPTION_TAP_MAX_CHANNEL_WORKERS`` override
        is read by ``CaptionTapWorkerSettings.from_env``, not by
        ``default_max_channel_workers`` itself -- confirm the settings seam
        still honours a value above the new flat default."""

        from civiccast.captions.tap_worker import CaptionTapWorkerSettings

        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "inline")
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_DIR", "/tmp/does-not-need-to-exist")
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_MAX_CHANNEL_WORKERS", "3")

        settings = CaptionTapWorkerSettings.from_env()

        assert settings.max_channel_workers == 3

    def test_startup_logs_cpu_count_and_the_chosen_sizing_in_one_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Item 79: one line at tap start records what box a run happened on.

        `getattr(runtime, "cpu_threads", "n/a")` reads the live sizing off
        whatever runtime was injected -- a real `FasterWhisperRuntime` has the
        attribute, the `_ScriptedRuntime` test double used elsewhere in this
        module does not, so this test uses a small stand-in that does.
        """

        monkeypatch.setattr("civiccast.captions.tap_worker.os.cpu_count", lambda: 8)

        class _RuntimeWithCpuThreads(_ScriptedRuntime):
            cpu_threads = 1
            num_workers = 1

        with caplog.at_level("INFO", logger="civiccast.captions.tap_worker"):
            _worker(
                tmp_path / "tap",
                _RuntimeWithCpuThreads(),
                InMemoryCaptionReviewStore(),
                max_channel_workers=1,
            )

        assert "Caption tap starting" in caplog.text
        assert "cpu_count=8" in caplog.text
        assert "max_channel_workers=1" in caplog.text
        assert "runtime_workers=1" in caplog.text
        assert "live_cpu_threads=1" in caplog.text

    def test_backlog_fails_closed_instead_of_publishing_stale_captions(
        self,
        tmp_path: Path,
    ) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore())
        active = _active_vtt(tap_root, "government")
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text(
            "WEBVTT\n\nold\n00:00:00.000 --> 00:00:01.000\nstale caption\n",
            encoding="utf-8",
        )
        # A backlog that is over the limit on the WHOLE persistence window (U11
        # B2), with exactly 3 settled segments standing at the tripping scan.
        result, next_index = _trip_overload(worker, tap_root, "government")

        assert result.consumed_segments == 0
        assert getattr(result, "dropped_overload_segments", 0) == 3
        assert getattr(result, "overloaded_channels", ()) == ("government",)
        assert load_caption_cues_from_timed_text(active, source_id="government") == []
        # DISCARDED, not filed under `overload/`. Nothing ever swept that
        # directory (the retention policy tap sweep reads `processed/` only)
        # and no review row referenced it, so it grew without bound on a
        # chronically overloaded station -- see TestCaptionTapPlayoutProtection
        # for the two-pause-cycle proof.
        assert not (tap_root / "government" / "overload").exists()
        # Everything the gate QUEUED is gone; the newest file survives because
        # the settle guard never queued it.
        assert [path.name for path in (tap_root / "government").glob("chunk-*.wav")] == [
            f"chunk-{next_index - 1:06d}.wav"
        ]
        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        # An overload now OPENS A BACKOFF PAUSE rather than merely reporting
        # itself and retrying on the next scan, so the operator-visible state
        # is "paused" and it says when captions will be attempted again.
        assert status["state"] == "paused"
        assert status["backlog_segments"] == 3
        assert status["consecutive_overloads"] == 1
        assert status["resume_in_seconds"] > 0
        assert result.paused_channels == ("government",)

    def test_the_operator_switch_stops_asr_without_a_restart(self, tmp_path: Path) -> None:
        """``StationProfile.live_captions_enabled`` is read every scan.

        An activated native station forces ``CIVICCAST_CAPTION_TAP=inline``
        into its environment, so this switch is the operator's only way to
        stop live captioning -- and it has to take effect on a station that is
        ON AIR, without restarting the control plane.
        """

        tap_root = tmp_path / "tap"
        enabled = [True]
        runtime = _ScriptedRuntime()
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            is_enabled=lambda: enabled[0],
        )
        active = _active_vtt(tap_root, "government")

        for index in range(3):
            _write_wav(tap_root / "government" / f"chunk-{index:06d}.wav")
        assert worker.run_once().consumed_segments == 2
        assert len(runtime.seen_chunks) == 2
        assert load_caption_cues_from_timed_text(active, source_id="government") != []

        enabled[0] = False
        for index in range(3, 4):
            _write_wav(tap_root / "government" / f"chunk-{index:06d}.wav")
        disabled = worker.run_once()

        assert disabled.consumed_segments == 0
        assert len(runtime.seen_chunks) == 2
        # Nothing on air claims to be captioned any more...
        assert load_caption_cues_from_timed_text(active, source_id="government") == []
        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"
        assert json.loads(status_path.read_text(encoding="utf-8"))["state"] == "disabled"
        # ...and the forked audio is DELETED rather than filed as evidence: a
        # station that switched live captions off has not asked CivicCast to
        # keep a rolling recording of its broadcast audio.
        assert not (tap_root / "government" / "overload").exists()
        assert sorted(path.name for path in (tap_root / "government").glob("chunk-*.wav")) == [
            "chunk-000003.wav"
        ]

        # Switching it back on resumes, still without a restart.
        enabled[0] = True
        for index in range(4, 6):
            _write_wav(tap_root / "government" / f"chunk-{index:06d}.wav")
        assert worker.run_once().consumed_segments == 2

    def test_an_overloaded_channel_spends_no_asr_while_paused(self, tmp_path: Path) -> None:
        """The pause must be real: not one sample reaches the runtime.

        The field defect was not that overload went undetected -- it was that
        the worker detected overload, cleared the captions, dropped the audio,
        logged CRITICAL, and then immediately tried again, forever, at full
        CPU. Backing off is what makes "captions are best effort" true.
        """

        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        policy = CaptionBackoffPolicy(base_seconds=60.0, monotonic=clock)
        runtime = _ScriptedRuntime()
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            backoff_policy=policy,
        )

        # A SUSTAINED overshoot (U11 B2) opens the pause; a one-scan overshoot
        # no longer does.
        first, next_index = _trip_overload(worker, tap_root, "government")
        assert first.overloaded_channels == ("government",)
        assert first.consumed_segments == 0
        # The persistence window TRANSCRIBED the oldest segments of each
        # over-limit scan instead of deleting them -- that deferral is the whole
        # point of U11 B2, so the baseline is taken here, at the pause. What
        # this test proves is that the PAUSE ITSELF spends nothing.
        transcribed_at_pause = len(runtime.seen_chunks)

        # Second scan, still inside the 60s window: a fresh, WITHIN-capacity
        # backlog arrives and is still not transcribed. Clean slate first --
        # the tripping scan left its newest file behind (the settle guard never
        # queued it) and that is not part of this scan's backlog.
        for path in (tap_root / "government").glob("chunk-*.wav"):
            path.unlink()
        clock.advance(2.0)
        for index in range(next_index, next_index + 3):
            _write_wav(tap_root / "government" / f"chunk-{index:06d}.wav")
        second = worker.run_once()

        assert second.paused_channels == ("government",)
        assert second.overloaded_channels == ()
        assert second.consumed_segments == 0
        assert len(runtime.seen_chunks) == transcribed_at_pause
        # Drained, not hoarded: a paused channel must not fill the disk while
        # the audio tap keeps publishing a segment every few seconds into it.
        # The per-scan drain DELETES (`overload/` is swept by nothing); only
        # the one-off move that opened the pause files evidence there.
        assert second.dropped_overload_segments == 2
        assert not (tap_root / "government" / "overload").exists()
        # Only the newest segment -- the one the settle guard always keeps back
        # for a later scan -- survives the drain.
        assert [path.name for path in (tap_root / "government").glob("chunk-*.wav")] == [
            f"chunk-{next_index + 2:06d}.wav"
        ]  # the two the drain deleted were the settled pair

        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["state"] == "paused"
        assert status["resume_in_seconds"] == 58.0

    def test_captions_resume_after_the_backoff_window_when_the_backlog_is_clear(
        self,
        tmp_path: Path,
    ) -> None:
        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        policy = CaptionBackoffPolicy(base_seconds=60.0, monotonic=clock)
        runtime = _ScriptedRuntime()
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            backoff_policy=policy,
        )

        next_index = _trip_overload(worker, tap_root, "government")[1]

        transcribed_while_paused = len(runtime.seen_chunks)

        # Clean slate: the tripping scan's newest file is not this scan's backlog.
        for path in (tap_root / "government").glob("chunk-*.wav"):
            path.unlink()
        clock.advance(61.0)
        for index in range(next_index, next_index + 3):
            _write_wav(tap_root / "government" / f"chunk-{index:06d}.wav")
        resumed = worker.run_once()

        assert resumed.paused_channels == ()
        assert resumed.consumed_segments == 2
        assert len(runtime.seen_chunks) == transcribed_while_paused + 2
        # The audio that arrived after the pause ended is what got transcribed.
        # (A stabilizer window can report a segment as an ``-overlap`` variant,
        # so the id is matched by prefix rather than exactly.)
        resumed_ids = [chunk.chunk_id for chunk in runtime.seen_chunks[transcribed_while_paused:]]
        for offset in (0, 1):
            assert any(
                chunk_id.startswith(f"government-tap-{next_index + offset:06d}")
                for chunk_id in resumed_ids
            ), (offset, resumed_ids)
        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["state"] == "within-capacity"

    def test_a_repeated_overload_escalates_the_pause(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        policy = CaptionBackoffPolicy(base_seconds=60.0, monotonic=clock)
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            backoff_policy=policy,
        )
        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"

        next_index = 0
        for expected_pause in (60.0, 120.0, 240.0):
            # Each rung has to be EARNED the same way: a sustained overshoot for
            # the whole persistence window (U11 B2), from a channel that is no
            # longer paused because the clock has passed the previous rung.
            next_index = _trip_overload(worker, tap_root, "government", first_index=next_index)[1]
            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "paused"
            assert status["resume_in_seconds"] == expected_pause
            clock.advance(expected_pause + 1.0)

    def test_the_overload_is_logged_once_per_pause_not_every_scan(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """663 caption lines in one control-plane log is the defect.

        The overload line was CRITICAL and repeated every ~30s for all three
        channels while the actual casualty -- a playout worker restarting --
        was buried in it. One WARNING per pause window, and nothing at all
        while the pause holds.
        """

        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            backoff_policy=CaptionBackoffPolicy(base_seconds=60.0, monotonic=clock),
        )

        with caplog.at_level(logging.DEBUG, logger="civiccast.captions.tap_worker"):
            next_index = _trip_overload(worker, tap_root, "government")[1]
            for scan in range(5):
                clock.advance(2.0)
                for index in range(next_index + scan * 2, next_index + 2 + scan * 2):
                    _write_wav(tap_root / "government" / f"chunk-{index:06d}.wav")
                worker.run_once()

        overload_records = [
            record for record in caplog.records if "Caption tap overload" in record.getMessage()
        ]
        assert len(overload_records) == 1
        assert overload_records[0].levelno == logging.WARNING
        assert not [record for record in caplog.records if record.levelno >= logging.CRITICAL]

    def test_a_bad_segment_is_quarantined_not_fatal(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        store = InMemoryCaptionReviewStore()
        worker = _worker(tap_root, runtime, store)
        bad = tap_root / "gov-ch12" / "chunk-000000.wav"
        _write_wav(tap_root / "gov-ch12" / "chunk-000001.wav")
        _write_wav(tap_root / "gov-ch12" / "chunk-000002.wav")
        bad.write_bytes(b"this is not a wav file")
        assert bad.is_file()

        result = worker.run_once()

        # The good settled segment was still consumed.
        assert result.consumed_segments == 1
        assert result.quarantined_segments == 1
        assert len(runtime.seen_chunks) == 1

    def test_discovers_unbounded_chunk_indices_after_six_digit_rollover(
        self,
        tmp_path: Path,
    ) -> None:
        """`chunk-%06d` is minimum width, so 1000000 must not disappear."""

        tap_root = tmp_path / "tap"
        runtime = _ScriptedRuntime()
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            atomic_segments=True,
        )
        for index in (999999, 1_000_000):
            _write_wav(tap_root / "government" / f"chunk-{index}.wav")

        result = worker.run_once()

        assert result.consumed_segments == 2
        assert [chunk.chunk_id for chunk in runtime.seen_chunks] == [
            "government-tap-999999",
            "government-tap-1000000-overlap",
        ]
        assert [chunk.start_seconds for chunk in runtime.seen_chunks] == [999999.0, 999999.0]
        assert sorted(
            path.name for path in (tap_root / "government" / "processed").glob("*.wav")
        ) == ["chunk-1000000.wav", "chunk-999999.wav"]

    def test_quarantines_restarted_chunk_collision_instead_of_overwriting_evidence(
        self,
        tmp_path: Path,
    ) -> None:
        tap_root = tmp_path / "tap"
        original = tap_root / "government" / "chunk-000007.wav"
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            atomic_segments=True,
        )
        _write_wav(original)
        assert worker.run_once().consumed_segments == 1

        # A restarted producer reused the same index with distinct bytes: it
        # writes the colliding chunk AFTER the replacement worker started, which
        # is the only ordering a live writer can produce. (A chunk already on
        # disk when a worker starts is a leftover from a finished session and is
        # discarded at startup -- see the B1 startup-leftovers test.)
        restarted_worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            atomic_segments=True,
        )
        _write_wav(original, seconds=2.0)

        result = restarted_worker.run_once()

        assert result.consumed_segments == 0
        assert result.quarantined_segments == 1
        assert (tap_root / "government" / "collision" / "chunk-000007.wav").is_file()


class TestCaptionTapPlayoutProtection:
    """The four behaviours that keep captions from costing the station its air."""

    def test_retention_still_prunes_while_live_captions_are_switched_off(
        self, tmp_path: Path
    ) -> None:
        """Turning captions off is a decision about FUTURE transcription.

        It is not a licence to stop deleting audio the station has already
        recorded. Gating the retention sweep behind the enabled check froze
        every retention clock (spec 4.3) for as long as the switch was off.
        """

        tap_root = tmp_path / "tap"

        enforced: list[Path] = []

        class _RecordingRetention:
            def enforce_discovered(self, *, tap_root, review_store, segment_seconds):  # type: ignore[no-untyped-def]
                enforced.append(tap_root)
                return SimpleNamespace(ready=True, refusal_reason=None)

            def record_event(self, **_kwargs: object) -> None:  # pragma: no cover
                return None

        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=1.0,
            retention_policy=_RecordingRetention(),  # type: ignore[arg-type]
            is_enabled=lambda: False,
        )
        _write_wav(tap_root / "government" / "chunk-000000.wav")
        _write_wav(tap_root / "government" / "chunk-000001.wav")

        result = worker.run_once()

        assert result.consumed_segments == 0  # captions really are off
        assert enforced == [tap_root]  # ...and retention really did run

    def test_a_chronically_overloaded_channel_retains_no_audio_across_pause_cycles(
        self, tmp_path: Path
    ) -> None:
        """`<channel>/overload/` was swept by nothing, and it grew forever.

        The retention policy tap sweep reads `processed/` only, so BOTH the
        move that opened a pause and the per-scan drain that followed it piled
        raw broadcast audio into `overload/` with no retention clock on it and
        no review row referencing it. On a station that cannot keep up -- the
        one case this whole change exists for -- that repeats every pause
        cycle, forever. This drives TWO full cycles and asserts that nothing
        accumulates on either path.
        """

        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        channel = tap_root / "government"
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            backoff_policy=CaptionBackoffPolicy(base_seconds=60.0, monotonic=clock),
            monotonic=clock,
        )

        next_index = 0
        for cycle in range(2):
            # Sustained overload -> opens a pause (60s, then 120s).
            result, next_index = _trip_overload(
                worker, tap_root, "government", first_index=next_index
            )
            assert result.overloaded_channels == ("government",), f"cycle {cycle}"

            # Several scans inside the pause window -> per-scan drain.
            for _ in range(5):
                clock.advance(2.0)
                for _ in range(2):
                    _write_wav(channel / f"chunk-{next_index:06d}.wav")
                    next_index += 1
                worker.run_once()

            # Nothing retained, on either path, in either cycle.
            assert not (channel / "overload").exists(), f"cycle {cycle}"
            clock.advance(60.0 * (2**cycle) + 1.0)

        # The tap directory itself never accumulated either: at most the one
        # unsettled newest segment is left behind.
        assert len(list(channel.glob("chunk-*.wav"))) <= 1

    def test_the_retention_sweep_runs_on_its_own_slower_cadence(self, tmp_path: Path) -> None:
        """Retention enforces a schedule in DAYS; it must not run 30x a minute.

        `enforce_discovered` lists review rows from the database and SHA-256s
        every chunk it considers. At the 2-second scan cadence that is a query
        plus a pass over the recorded audio 30 times a minute, forever.
        """

        tap_root = tmp_path / "tap"
        tap_root.mkdir(parents=True)
        clock = _FakeClock()
        sweeps: list[float] = []

        class _CountingRetention:
            def enforce_discovered(self, **_kwargs: object):  # type: ignore[no-untyped-def]
                sweeps.append(clock.now)
                return SimpleNamespace(ready=True, refusal_reason=None)

            def record_event(self, **_kwargs: object) -> None:  # pragma: no cover
                return None

        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=1.0,
            retention_policy=_CountingRetention(),  # type: ignore[arg-type]
            monotonic=clock,
        )

        # The FIRST scan always sweeps -- a station must not wait a minute
        # after startup to start honouring its retention schedule.
        worker.run_once()
        assert sweeps == [0.0]

        for _ in range(29):  # 58 seconds of 2-second scans
            clock.advance(2.0)
            worker.run_once()
        assert sweeps == [0.0]

        clock.advance(4.0)
        worker.run_once()
        # Periodic verification is asynchronous; observe its completed result,
        # not whether the new thread happened to win the scheduling race.
        assert worker.wait_for_retention_sweep(timeout=10.0)
        assert sweeps == [0.0, 62.0]

    def test_a_storage_refusal_is_not_forgotten_between_retention_sweeps(
        self, tmp_path: Path
    ) -> None:
        """Failing closed must survive the cadence.

        A scan that skips the sweep must keep the last verdict, not quietly
        revert to "ready" and start captioning again on storage the policy
        already refused.
        """

        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        runtime = _ScriptedRuntime()

        class _RefusingRetention:
            def enforce_discovered(self, **_kwargs: object):  # type: ignore[no-untyped-def]
                return SimpleNamespace(ready=False, refusal_reason="disk full")

            def record_event(self, **_kwargs: object) -> None:  # pragma: no cover
                return None

        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=runtime,
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=1.0,
            retention_policy=_RefusingRetention(),  # type: ignore[arg-type]
            monotonic=clock,
        )
        _write_wav(tap_root / "government" / "chunk-000000.wav")
        _write_wav(tap_root / "government" / "chunk-000001.wav")

        assert worker.run_once().consumed_segments == 0
        clock.advance(2.0)  # inside the sweep cadence: no fresh verdict
        assert worker.run_once().consumed_segments == 0
        assert runtime.seen_chunks == []
        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["state"] == "storage-refused"
        assert status["refusal_reason"] == "disk full"

    def test_an_unchanged_status_is_not_rewritten_every_scan(self, tmp_path: Path) -> None:
        """A steady state is not news; a durable write per scan is a cost."""

        tap_root = tmp_path / "tap"
        clock = _FakeClock()
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            is_enabled=lambda: False,
            monotonic=clock,
        )
        _write_wav(tap_root / "government" / "chunk-000000.wav")
        _write_wav(tap_root / "government" / "chunk-000001.wav")
        status_path = tap_root.parent / "egress" / "government" / "captions" / "runtime-status.json"

        worker.run_once()
        first = json.loads(status_path.read_text(encoding="utf-8"))["updated_at"]

        for _ in range(5):
            clock.advance(2.0)
            _write_wav(tap_root / "government" / f"chunk-{uuid.uuid4().int % 900000:06d}.wav")
            worker.run_once()

        assert json.loads(status_path.read_text(encoding="utf-8"))["updated_at"] == first

        # ...but it does not go stale forever: a heartbeat republishes it.
        clock.advance(31.0)
        _write_wav(tap_root / "government" / "chunk-000900.wav")
        _write_wav(tap_root / "government" / "chunk-000901.wav")
        worker.run_once()
        assert json.loads(status_path.read_text(encoding="utf-8"))["updated_at"] != first

    def test_switching_captions_off_clears_a_channel_with_no_tap_directory(
        self, tmp_path: Path
    ) -> None:
        """Clear by the CHANNEL SET, not by tap-directory presence.

        A channel whose tap directory was never created (or was already swept)
        has no directory to iterate -- yet it can still be serving a stale
        `active.vtt` from before the switch was thrown. Captions on air that
        nothing is producing any more is the exact failure the fail-closed
        clear exists to prevent.
        """

        tap_root = tmp_path / "tap"
        tap_root.mkdir(parents=True)
        stale = _active_vtt(tap_root, "education")
        stale.parent.mkdir(parents=True)
        stale.write_text(
            "WEBVTT\n\nold\n00:00:00.000 --> 00:00:01.000\nstale caption\n",
            encoding="utf-8",
        )
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            is_enabled=lambda: False,
        )

        result = worker.run_once()

        assert result.channels == ()  # no tap directory for this channel at all
        assert load_caption_cues_from_timed_text(stale, source_id="education") == []

    def test_switching_captions_off_clears_a_stale_sidecar_when_the_tap_root_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Round-2 review MAJOR 3: the enabled check must run BEFORE the
        tap-directory check. With the old order, a station whose tap root was
        never created (or was swept) returned early with live captions off,
        ``_run_disabled`` never ran, the stale ``active.vtt`` was never blanked,
        and the caption feed kept re-sending every cue in it every 2 s."""

        tap_root = tmp_path / "tap-not-created"
        assert not tap_root.exists()
        stale = _active_vtt(tap_root, "government")
        stale.parent.mkdir(parents=True)
        stale.write_text(
            "WEBVTT\n\nold\n00:00:00.000 --> 00:00:01.000\nstale caption\n",
            encoding="utf-8",
        )
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            is_enabled=lambda: False,
        )

        result = worker.run_once()

        assert result.channels == ()
        assert not tap_root.exists()  # never created as a side effect
        assert load_caption_cues_from_timed_text(stale, source_id="government") == []

    def test_a_missing_tap_root_still_idles_cleanly_when_captions_are_on(
        self, tmp_path: Path
    ) -> None:
        tap_root = tmp_path / "tap-not-created"
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            is_enabled=lambda: True,
        )

        result = worker.run_once()

        assert result == CaptionTapScanResult()
        assert not tap_root.exists()


class TestCaptionTapWorkerSettings:
    def test_defaults_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP", raising=False)
        settings = CaptionTapWorkerSettings.from_env()
        assert settings.mode == "off"

    def test_inline_requires_the_tap_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "inline")
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP_DIR", raising=False)
        with pytest.raises(ValueError, match="CIVICCAST_CAPTION_TAP_DIR"):
            CaptionTapWorkerSettings.from_env()

    def test_off_with_no_tap_dir_parses_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Item 91: this is exactly what a station-set with the off switch
        looks like -- ``CIVICCAST_CAPTION_TAP=off`` and no
        ``CIVICCAST_CAPTION_TAP_DIR`` at all (``station_runtime`` omits it
        entirely rather than setting it to an empty string). The fail-fast
        dir check is gated on ``mode != TAP_MODE_OFF``, so this must parse
        cleanly with ``tap_root=None`` rather than raising -- and
        ``civiccast.app`` never calls ``build_tap_worker`` in this case
        (gated on ``mode == "inline"``), so a missing tap_root here can never
        reach ``build_tap_worker``'s own ``ValueError`` guard in production."""

        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP_DIR", raising=False)

        settings = CaptionTapWorkerSettings.from_env()

        assert settings.mode == "off"
        assert settings.tap_root is None

    def test_invalid_mode_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "sometimes")
        with pytest.raises(ValueError, match="CIVICCAST_CAPTION_TAP"):
            CaptionTapWorkerSettings.from_env()

    def test_inline_with_dir_parses(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "inline")
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_DIR", str(tmp_path))
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_POLL_SECONDS", "2.5")
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_ATOMIC", "1")
        settings = CaptionTapWorkerSettings.from_env()
        assert settings.mode == "inline"
        assert settings.tap_root == tmp_path
        assert settings.poll_seconds == 2.5
        assert settings.atomic_segments is True

    def test_channel_override_does_not_override_runtime_capacity(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A channel-pool override must not rewrite CTranslate2 capacity.

        The runtime now chooses its own hardware profile: one CTranslate2
        worker on CPU and three on CUDA.  ``max_channel_workers`` controls the
        tap executor separately.  This boundary keeps an explicit tap-pool
        override from silently changing model capacity in either direction.
        """

        captured: dict[str, object] = {}
        runtime = _ScriptedRuntime()

        def runtime_factory(**kwargs: object) -> _ScriptedRuntime:
            captured.update(kwargs)
            return runtime

        monkeypatch.setattr(
            "civiccast.captions.runtime.FasterWhisperRuntime",
            runtime_factory,
        )
        settings = CaptionTapWorkerSettings(
            mode="inline",
            tap_root=tmp_path / "tap",
            max_channel_workers=3,
        )

        build_tap_worker(
            settings,
            InMemoryCaptionReviewStore(),
            caption_work_dir=tmp_path / "egress",
        )

        # The runtime is constructed with NO worker override: it selects one
        # or three from its actual device. Asserting the whole dict (not just
        # the absence of a key) also catches a future caller quietly coupling
        # these two controls again under a different argument name.
        #
        # What the live tap DOES pass is the CPU budget, and only that: one
        # CTranslate2 intra-thread and greedy decoding. The batch/VOD defaults
        # (`cpu_threads=0`, i.e. every core, and beam 5) are what produced the
        # measured field failure -- ~247% of a core against three playout
        # workers being killed by their own stall watchdog.
        #
        # It asks for that sizing by declaring itself LIVE, not by passing a
        # kwargs bundle: this construction branch is dead in the native
        # service (the app pre-builds the runtime through
        # `civiccast.ai_models.runtime.build_caption_runtime` and injects it),
        # so kwargs here proved nothing about the product. `live=True` is a
        # property of the runtime and travels with it through BOTH paths --
        # see tests/ai_models/test_runtime_wiring.py for the app's own call.
        assert captured == {"live": True}

    def test_native_station_rejects_the_unaccepted_vulkan_runtime(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("CIVICCAST_NATIVE_STATION", "1")
        monkeypatch.setenv("CIVICCAST_CAPTION_RUNTIME", "whispercpp-vulkan")
        monkeypatch.setenv(
            "CIVICCAST_WHISPER_CPP_EXE",
            str(tmp_path / "caption-pack" / "whisper-cli.exe"),
        )
        monkeypatch.setenv(
            "CIVICCAST_WHISPER_CPP_MODEL_PATH",
            str(tmp_path / "caption-pack" / "ggml-large-v3-q5_0.bin"),
        )
        settings = CaptionTapWorkerSettings(
            mode="inline",
            tap_root=tmp_path / "tap",
        )

        with pytest.raises(ValueError, match="accepted faster-whisper"):
            build_tap_worker(
                settings,
                InMemoryCaptionReviewStore(),
                caption_work_dir=tmp_path / "egress",
            )

    def test_unknown_caption_runtime_fails_fast(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("CIVICCAST_CAPTION_RUNTIME", "mystery")
        settings = CaptionTapWorkerSettings(
            mode="inline",
            tap_root=tmp_path / "tap",
        )

        with pytest.raises(ValueError, match="CIVICCAST_CAPTION_RUNTIME"):
            build_tap_worker(
                settings,
                InMemoryCaptionReviewStore(),
                caption_work_dir=tmp_path / "egress",
            )


class TestFirstRetentionSweepIsBounded:
    """beta.9 N=3 field defect (Blackwell 2026-09-18, three channels ON_AIR).

    The FIRST retention verification ran SYNCHRONOUSLY on the scan thread "by
    design", on the rationale that it happens at session start before there are
    live captions to lose.  Measured live: it runs at WORKER CONSTRUCTION
    against a 13,550-candidate archive and took 16.75 s -- >3x the 5 s segment
    cadence -- so 3+ settled segments accumulated before the first scan reached
    the max-2 backlog gate and the gate tripped at construction.  That is the
    startup overload, measured.

    The fix keeps the safety invariant (no ASR into an unverified store) but
    stops the scan thread from being blocked for the whole sweep: the first
    sweep runs on the same daemon thread later sweeps already use, and the scan
    waits only a bound far below the segment cadence.
    """

    def test_first_sweep_does_not_block_the_scan_thread(self, tmp_path: Path) -> None:
        """RED on the pre-fix code: dispatch returned only after the sweep."""

        tap_root = tmp_path / "tap"
        (tap_root / "public").mkdir(parents=True)
        slow = _SlowRetentionPolicy(delay_seconds=3.0)
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            retention_policy=slow,  # type: ignore[arg-type]
            monotonic=_FakeClock(),  # type: ignore[arg-type]
        )
        _write_wav(tap_root / "public" / "chunk-000000.wav", seconds=5.0)

        started = time.monotonic()
        with worker._phase_timing.phase("retention_dispatch"):
            worker._sweep_retention()
        elapsed = time.monotonic() - started

        assert elapsed < 2.0, (
            "the first retention dispatch must not block the scan thread for the "
            f"whole sweep; it blocked {elapsed:.2f}s against a {slow.delay}s sweep"
        )
        # The sweep really is slow and really did run -- so the bound, not a
        # fast sweep, is what kept the dispatch short.
        worker.wait_for_retention_sweep(timeout=10.0)
        assert slow.calls == 1

    def test_slow_first_sweep_allows_text_only_captions_without_audio(self, tmp_path: Path) -> None:
        """Pending verification keeps captions live without retaining audio.

        A slow first sweep is not a storage refusal.  The worker may emit
        caption text while the verdict is in flight, but it must not retain
        raw segments or audio evidence until the store is verified.
        """

        tap_root = tmp_path / "tap"
        (tap_root / "public").mkdir(parents=True)
        slow = _SlowRetentionPolicy(delay_seconds=3.0)
        slow.release = threading.Event()
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            retention_policy=slow,  # type: ignore[arg-type]
            monotonic=_FakeClock(),  # type: ignore[arg-type]
        )
        for index in range(2):
            _write_wav(tap_root / "public" / f"chunk-{index:06d}.wav", seconds=5.0)

        try:
            result = worker.run_once()

            # The first verdict is deliberately still in flight, so text is
            # allowed but audio evidence and processed WAVs are not.
            assert slow.entered.wait(timeout=2.0)
            assert result.consumed_segments == 2
            assert worker._retention_in_flight
            assert not list((tap_root / "public" / "processed").glob("*.wav"))
            assert not list((tap_root.parent / "egress").rglob("evidence/*.wav"))
            items = worker._review_store.list()
            assert items
            assert all(not item.audio_evidence_available for item in items)
            assert _active_vtt(tap_root, "public").read_text().strip() != "WEBVTT"
            assert worker._retention_ready is False
            assert worker._retention_verified is False
            assert worker._retention_refusal == "retention-verification-pending"
        finally:
            slow.release.set()
            assert worker.wait_for_retention_sweep(timeout=10.0)
        assert worker._retention_verified is True
        assert worker._retention_ready is True


class TestAsyncCaptionQueue:
    """The live scan must continue while bounded per-channel ASR is running."""

    def test_inflight_segments_are_not_counted_as_queued_overload(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        channel = "public"
        channel_dir = tap_root / channel
        channel_dir.mkdir(parents=True)
        runtime = _BlockingRuntime()
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tmp_path / "egress",
            runtime=runtime,
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            max_channel_workers=1,
            retention_policy=_SlowRetentionPolicy(delay_seconds=0.0),
        )
        # Establish the retention verdict before the held ASR call.  This keeps
        # the test focused on queue accounting, not the storage gate.
        worker._sweep_retention()
        assert worker.wait_for_retention_sweep(timeout=2.0)

        for index in range(2):
            _write_wav(channel_dir / f"chunk-{index:06d}.wav", seconds=5.0)

        first = worker.run_once(wait_for_results=False)
        assert first.overloaded_channels == ()
        assert runtime.started.wait(timeout=2.0)

        # Both settled files belong to the in-flight batch.  A new settled file
        # must therefore be a one-file queued backlog, not a three-file overload.
        _write_wav(channel_dir / "chunk-000002.wav", seconds=5.0)
        second = worker.run_once(wait_for_results=False)
        assert second.overloaded_channels == ()
        assert second.paused_channels == ()

        runtime.release.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not worker._channel_futures:
                break
            time.sleep(0.02)
        assert not worker._channel_futures
        assert list((channel_dir / "processed").glob("*.wav"))

    @pytest.mark.parametrize(
        ("elapsed_seconds", "expected_dropped"),
        [(30.0, 0), (210.0, 1)],
        ids=("brief-slow-cycle", "grace-expired"),
    )
    def test_whistle_backlog_waits_for_its_finite_grace_before_shedding(
        self,
        tmp_path: Path,
        elapsed_seconds: float,
        expected_dropped: int,
    ) -> None:
        tap_root = tmp_path / "tap"
        channel = "public"
        channel_dir = tap_root / channel
        channel_dir.mkdir(parents=True)
        runtime = _WhistleBacklogRuntime()
        clock = _FakeClock()
        worker = CaptionTapWorker(
            retain_review_evidence=False,
            tap_root=tap_root,
            caption_work_dir=tmp_path / "egress",
            runtime=runtime,
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            max_channel_workers=1,
            monotonic=clock,
        )

        try:
            for index in range(2):
                _write_wav(channel_dir / f"chunk-{index:06d}.wav", seconds=5.0)
            worker.run_once(wait_for_results=False)
            assert runtime.started.wait(timeout=2.0)

            # The first two chunks are in the blocked batch. Three further
            # settled chunks exceed the tap's ordinary max-2 backlog while the
            # request runs. Fifteen scans reproduce the shipped 30s threshold.
            for index in range(2, 5):
                _write_wav(channel_dir / f"chunk-{index:06d}.wav", seconds=5.0)
            for _ in range(_OVERLOAD_PERSISTENCE_WINDOW):
                worker.run_once(wait_for_results=False)

            clock.advance(elapsed_seconds)
            runtime.release.set()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and worker._channel_futures:
                time.sleep(0.02)
            assert not worker._channel_futures

            result = worker.run_once(wait_for_results=False)
            assert result.dropped_overload_segments == expected_dropped
            assert result.overloaded_channels == ()
        finally:
            runtime.release.set()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and worker._channel_futures:
                time.sleep(0.02)
            worker._shutdown_channel_executor()

    @pytest.mark.parametrize("retain_review_evidence", [False, True])
    def test_whistle_waiting_audio_cap_matches_its_grace_in_both_retention_modes(
        self,
        tmp_path: Path,
        retain_review_evidence: bool,
    ) -> None:
        tap_root = tmp_path / "tap"
        channel_dir = tap_root / "public"
        channel_dir.mkdir(parents=True)
        runtime = _WhistleBacklogRuntime()
        worker = CaptionTapWorker(
            retain_review_evidence=retain_review_evidence,
            tap_root=tap_root,
            caption_work_dir=tmp_path / "egress",
            runtime=runtime,
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            atomic_segments=True,
            max_channel_workers=1,
        )
        for index in range(45):
            _write_wav(channel_dir / f"chunk-{index:06d}.wav", seconds=5.0)

        settled = worker._settled_segments(channel_dir)

        assert len(settled) == 42
        assert settled[0][0] == 3


class TestPublishFailureDoesNotAbortThePass:
    """Field defect (beta.9 three-channel ladder, 2026-09-19).

    Live evidence: three `Caption tap scan failed` occurrences in the installed
    service, all `PermissionError [WinError 5]` raised by
    `live_sidecar._atomic_write_text` at `temporary.replace(destination)`.
    ``run_once`` submits every channel through one ``pool.map``, so a single
    channel's sidecar-publish failure unwound the WHOLE pass: the other two
    channels lost their cue publication, backlog accounting, and gate
    evaluation for that scan. One transient file error must cost, at most, the
    one channel that hit it.
    """

    def test_one_channel_publish_failure_does_not_abort_the_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from civiccast.captions.live_sidecar import LiveWebVttPublisher

        tap_root = tmp_path / "tap"
        channels = ("public", "government", "education")
        worker = _worker(
            tap_root,
            _ScriptedRuntime(text="the council will come to order"),
            InMemoryCaptionReviewStore(),
            max_channel_workers=3,
        )
        for channel in channels:
            # Three numbered segments: the settle guard drops the highest WAV
            # and the stabilizer needs consecutive passes to commit, matching
            # the working single-channel publish test above.
            for index in range(3):
                _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=1.0)
        # Establish the retention verdict first so the pass reaches the publish
        # path instead of the fail-closed retention-pending branch. Without this
        # the test would exercise the wrong seam entirely (found during RED
        # review: the first version of this test was mis-aimed).
        worker._sweep_retention()
        assert worker._retention_ready is True

        real_publish = LiveWebVttPublisher.publish

        def publish(self: LiveWebVttPublisher, cues):  # type: ignore[no-untyped-def]
            if "government" in self.active_path.parts:
                raise PermissionError("[WinError 5] Access is denied: active.vtt replace refused")
            real_publish(self, cues)

        monkeypatch.setattr(LiveWebVttPublisher, "publish", publish)

        result = worker.run_once()

        # The pass must survive: the other two channels still consumed their
        # segments and published real cues.
        assert result.consumed_segments == 4, (
            "a publish failure on one channel must not abort the other "
            "channels' consumption for the pass"
        )
        committed = {"public", "education"}
        assert committed.issubset(set(result.channels)), (
            "the surviving channels must still be reported in the pass result"
        )
        for channel in ("public", "education"):
            cues = load_caption_cues_from_timed_text(
                _active_vtt(tap_root, channel), source_id=channel
            )
            assert [cue.text for cue in cues] == ["the council will come to order"], (
                f"{channel} lost its cue publication because another channel's "
                "sidecar publish failed"
            )
        # The failing channel did not publish this pass -- it is isolated, not
        # silently counted as a success. Its sidecar was never created because
        # its very first publish (the reset) was refused.
        government_sidecar = _active_vtt(tap_root, "government")
        assert (
            not government_sidecar.exists()
            or load_caption_cues_from_timed_text(government_sidecar, source_id="government") == []
        )


class TestCaptionTapWorkerStartupLeftovers:
    """U11 B1: a worker must not inherit the previous session's audio.

    MEASURED on the live station (U10, 2026-09-24): the 17:17:41 and 17:17:43
    "Caption tap overload ... PAUSED" trips followed a service restart at
    ~17:17:2x, with the oldest queued segment 50.9-56.5 s old at trip time --
    i.e. the tap counted audio recorded BEFORE the restart as live backlog and
    paused captions for 120 s before a single new segment existed.

    The existing session-start discard did not cover this: it runs only from the
    daemon's explicit START hook, which a restart does not issue.
    """

    def test_a_worker_constructed_over_leftovers_does_not_pause_on_its_first_scan(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        (tap_root / channel).mkdir(parents=True)
        # Four segments left behind by the broadcast that just ended.
        for index in range(4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        runtime = _ScriptedRuntime()
        # Construction is inside the capture window on purpose: the startup
        # discard logs there.
        with caplog.at_level(logging.DEBUG, logger="civiccast.captions.tap_worker"):
            worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore())
            result = worker.run_once()

        # The acceptance rung for this unit is a grep of the station log for
        # "Caption tap overload ... PAUSED". Pin the same shape here: the
        # startup scan of a restarted tap must produce neither string.
        assert not [r for r in caplog.records if "Caption tap overload" in r.getMessage()], (
            "a restart over the previous broadcast's audio logged an overload"
        )
        assert not [r for r in caplog.records if "PAUSED" in r.getMessage()]
        # It DID say what it discarded, once, naming the boundary.
        discarded = [r for r in caplog.records if "discarded" in r.getMessage()]
        assert len(discarded) == 1, [r.getMessage() for r in discarded]
        assert "worker startup" in discarded[0].getMessage()

        assert result.dropped_overload_segments == 0
        assert result.overloaded_channels == (), (
            "audio left by a finished broadcast paused a freshly started tap"
        )
        assert result.paused_channels == ()
        assert runtime.seen_chunks == [], (
            "audio from a finished broadcast must not be transcribed into this session"
        )
        assert sorted(path.name for path in (tap_root / channel).glob("chunk-*.wav")) == []
        # Nothing was queued, so the channel published no status at all -- the
        # point is only that it never announced a pause. (An empty channel
        # writing no status is pre-existing behaviour, unchanged by this unit.)
        status_path = tap_root.parent / "egress" / channel / "captions" / "runtime-status.json"
        assert not status_path.exists() or (
            json.loads(status_path.read_text(encoding="utf-8"))["state"] != "paused"
        ), "a fresh tap that discarded the previous session's audio still announced a pause"

    def test_segments_written_after_construction_are_processed(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        (tap_root / channel).mkdir(parents=True)
        # Start over leftovers...
        for index in range(4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)
        runtime = _ScriptedRuntime()
        worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore())

        # ...then the tap writes the new session's audio, as it does in
        # production: the writer is a separate process that survives the restart.
        for index in range(4, 7):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        result = worker.run_once()

        # The settle guard keeps the newest file for a later scan; the two
        # settled new segments are transcribed.
        assert result.consumed_segments == 2
        assert [chunk.chunk_id for chunk in runtime.seen_chunks] == [
            "government-tap-000004",
            "government-tap-000005",
        ]

    def test_leftovers_in_one_channel_do_not_disturb_a_fresh_sibling(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        leftover_channel, fresh_channel = "public", "education"
        for index in range(6):
            _write_wav(tap_root / leftover_channel / f"chunk-{index:06d}.wav", seconds=5.0)

        runtime = _ScriptedRuntime()
        worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore())
        for index in range(2):
            _write_wav(tap_root / fresh_channel / f"chunk-{index:06d}.wav", seconds=5.0)

        result = worker.run_once()

        assert result.overloaded_channels == ()
        assert result.paused_channels == ()
        assert [chunk.chunk_id for chunk in runtime.seen_chunks] == ["education-tap-000000"]


class TestCaptionTapBacklogPersistence:
    """U11 B2: only a PERSISTENT overshoot may pause a channel.

    U10 measured 182 trips in a day with 70% of them at exactly 3 settled
    segments against a ``max_backlog_segments`` of 2 -- the minimum overshoot
    the gate can see -- on a box whose ASR pool was idle at trip time. A single
    over-limit scan is a transient (a slow batch, a retention sweep, a
    scheduling stall); a channel that stays over the limit for the whole
    persistence window is a genuine collapse.
    """

    def test_a_one_scan_overshoot_does_not_pause_a_channel(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        runtime = _ScriptedRuntime()
        worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore())
        # The exact measured shape: one settled segment over the max-2 limit.
        for index in range(4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        result = worker.run_once()

        assert result.overloaded_channels == ()
        assert result.paused_channels == ()
        assert result.dropped_overload_segments == 0, (
            "a single over-limit scan deleted live audio instead of transcribing it"
        )
        # Progress, not a stall: the oldest segments are transcribed now and the
        # excess stays queued.
        assert result.consumed_segments == 2
        assert len(runtime.seen_chunks) == 2
        status = _runtime_status(tap_root, channel)
        assert status["state"] == "within-capacity"
        assert status["backlog_segments"] <= status["max_backlog_segments"]

    def test_a_sustained_overshoot_trips_with_the_shipped_first_rung(self, tmp_path: Path) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        runtime = _ScriptedRuntime()
        worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore())

        _trip_overload(worker, tap_root, channel)

        status = _runtime_status(tap_root, channel)
        assert status["state"] == "paused"
        assert status["consecutive_overloads"] == 1
        # Unchanged product contract: the first pause is still the shipped base
        # window, still cleared, still escalated. Nothing about what a real
        # pause DOES is touched by this unit.
        assert status["resume_in_seconds"] == DEFAULT_BASE_BACKOFF_SECONDS
        assert load_caption_cues_from_timed_text(_active_vtt(tap_root, channel)) == []

    def test_the_streak_resets_when_the_backlog_drops_back_under_the_limit(
        self, tmp_path: Path
    ) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())

        next_index = _drive_to_the_edge_of_the_overload_window(worker, tap_root, channel)
        # The station catches up: nothing is queued, so the next scan is legal.
        for path in (tap_root / channel).glob("chunk-*.wav"):
            path.unlink()
        assert worker.run_once().overloaded_channels == ()

        # The whole window must now be earned AGAIN. If the earlier streak had
        # survived, this call's internal no-pause assertions would fail on its
        # first scan instead of running the full window.
        _trip_overload(worker, tap_root, channel, first_index=next_index)
        assert _runtime_status(tap_root, channel)["consecutive_overloads"] == 1

    def test_a_deferred_overshoot_spends_no_more_asr_than_a_legal_batch(
        self, tmp_path: Path
    ) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        runtime = _ScriptedRuntime()
        worker = _worker(tap_root, runtime, InMemoryCaptionReviewStore())
        for index in range(6):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        result = worker.run_once()

        # Six files -> five settled, of which only max_backlog_segments are
        # transcribed: the deferral must not turn one over-limit scan into a
        # six-segment ASR call on the box the pause exists to protect.
        assert result.consumed_segments == 2
        assert len(runtime.seen_chunks) == 2
        remaining = sorted(path.name for path in (tap_root / channel).glob("chunk-*.wav"))
        # Three settled segments the deferral left queued, plus the newest file
        # the settle guard always keeps back for a later scan.
        assert remaining == [
            "chunk-000002.wav",
            "chunk-000003.wav",
            "chunk-000004.wav",
            "chunk-000005.wav",
        ], f"the excess queue was dropped instead of being left for the next scan: {remaining}"

    def test_the_deferral_is_logged_once_per_episode_and_never_as_a_pause(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        worker = _worker(tap_root, _ScriptedRuntime(), InMemoryCaptionReviewStore())

        with caplog.at_level(logging.DEBUG, logger="civiccast.captions.tap_worker"):
            _drive_to_the_edge_of_the_overload_window(worker, tap_root, channel)

        behind = [
            record for record in caplog.records if "Caption tap is behind" in record.getMessage()
        ]
        assert len(behind) == 1, (
            "a transient overshoot must say so once, not on every scan of the window"
        )
        assert behind[0].levelno == logging.INFO
        # The operator's grep for a pause must not match a working tap.
        assert not [
            record for record in caplog.records if "Caption tap overload" in record.getMessage()
        ]
        assert not [record for record in caplog.records if "PAUSED" in record.getMessage()]

    def test_an_over_limit_scan_is_not_recovery_evidence_for_the_ladder(
        self, tmp_path: Path
    ) -> None:
        """A deferral must not walk the escalation ladder back down.

        The deferred path finishes real batches, so the ladder would otherwise
        see a long run of "healthy" scans in the middle of a station that is
        still behind -- and then trip again at the BASE window, which is exactly
        the flap the ladder exists to prevent. With a one-scan recovery window
        this is observable: if the deferrals counted, the second trip would open
        at 60 s instead of 120 s.
        """

        tap_root = tmp_path / "tap"
        channel = "government"
        clock = _FakeClock()
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            backoff_policy=CaptionBackoffPolicy(
                base_seconds=60.0, recovery_scans=1, recovery_windows=1, monotonic=clock
            ),
            monotonic=clock,
        )

        next_index = _trip_overload(worker, tap_root, channel)[1]
        assert _runtime_status(tap_root, channel)["resume_in_seconds"] == 60.0

        clock.advance(61.0)
        # K-1 scans that each completed a batch -- but every one of them was
        # over the limit, so none of them is recovery evidence.
        _trip_overload(worker, tap_root, channel, first_index=next_index)

        assert _runtime_status(tap_root, channel)["resume_in_seconds"] == 120.0, (
            "a deferral counted as within-capacity and retired a rung of the ladder"
        )

    def test_one_persistence_scan_restores_the_pre_u11_single_scan_contract(
        self, tmp_path: Path
    ) -> None:
        tap_root = tmp_path / "tap"
        channel = "government"
        worker = CaptionTapWorker(
            retain_review_evidence=True,
            tap_root=tap_root,
            caption_work_dir=tap_root.parent / "egress",
            runtime=_ScriptedRuntime(),
            review_store=InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            overload_persistence_scans=1,
            # The pre-U11 single-scan contract also needs catch-up off: with a
            # shed budget the one-scan overshoot would shed rather than pause,
            # which is a different question from the one this test asks (does
            # OVERLOAD_PERSISTENCE_SCANS=1 still restore the old rule?).
            catch_up_shed_limit=0,
        )
        for index in range(4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        result = worker.run_once()

        assert result.overloaded_channels == (channel,)
        assert _runtime_status(tap_root, channel)["state"] == "paused"

    def test_the_persistence_window_defaults_and_parses_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from civiccast.captions.tap_worker import DEFAULT_OVERLOAD_PERSISTENCE_SCANS

        # The test file's mirrored literal must equal the shipped default.
        assert DEFAULT_OVERLOAD_PERSISTENCE_SCANS == _OVERLOAD_PERSISTENCE_WINDOW
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP_OVERLOAD_PERSISTENCE_SCANS", raising=False)
        assert (
            CaptionTapWorkerSettings.from_env().overload_persistence_scans
            == DEFAULT_OVERLOAD_PERSISTENCE_SCANS
        )
        # 1 is legal (the escape hatch back to the pre-U11 rule), not a mistake.
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_OVERLOAD_PERSISTENCE_SCANS", "1")
        assert CaptionTapWorkerSettings.from_env().overload_persistence_scans == 1
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_OVERLOAD_PERSISTENCE_SCANS", "0")
        with pytest.raises(ValueError, match="OVERLOAD_PERSISTENCE_SCANS"):
            CaptionTapWorkerSettings.from_env()

    def test_recovery_scans_parse_from_the_environment_and_reach_the_backoff(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from civiccast.captions.tap_backoff import DEFAULT_RECOVERY_SCANS

        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP_RECOVERY_SCANS", raising=False)
        assert CaptionTapWorkerSettings.from_env().overload_recovery_scans == DEFAULT_RECOVERY_SCANS
        # A faster poll keeps the same recovery time span with more scans.
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_RECOVERY_SCANS", "12")
        assert CaptionTapWorkerSettings.from_env().overload_recovery_scans == 12

        worker = build_tap_worker(
            CaptionTapWorkerSettings(
                mode="inline", tap_root=tmp_path / "tap", overload_recovery_scans=12
            ),
            InMemoryCaptionReviewStore(),
            runtime=_ScriptedRuntime(),
            caption_work_dir=tmp_path / "egress",
        )
        assert worker._backoff._recovery_scans == 12


#: The three ON_AIR channels of the measured station (U22).
_CATCH_UP_CHANNELS = ("public", "government", "education")


class _TimedToneRuntime(_ScriptedRuntime):
    """One distinct word per whole second of audio, named by its audio second.

    The stabilizer commits only words that TWO windows agree on and matches them
    by exact token equality over overlapping word spans, so a fake runtime has to
    emit the SAME token for the same audio second every time it hears it -- the
    tap's 5 s overlap re-hears five of them on the next window. Naming a word
    ``s<second>`` for the ABSOLUTE audio second makes the seam assertable
    directly: a cue naming a second the catch-up discarded is a broken seam.

    Word timestamps come from the chunk the worker actually handed to ASR,
    including the overlap it prepended, so a window's words follow its real
    geometry rather than a guess about it.
    """

    def transcribe(
        self,
        chunks: Iterable[AudioChunk],
        vocabulary: CustomVocabulary | None = None,
    ) -> Iterable[CaptionHypothesis]:
        for chunk in chunks:
            self.seen_chunks.append(chunk)
            words = [
                CaptionWord(
                    text=f"s{second}",
                    start_seconds=float(second),
                    end_seconds=float(second + 1),
                    confidence=0.95,
                )
                for second in range(int(chunk.start_seconds), int(chunk.end_seconds))
            ]
            yield CaptionHypothesis(
                source_id=f"{chunk.chunk_id}-tap",
                start_seconds=chunk.start_seconds,
                end_seconds=chunk.end_seconds,
                text=" ".join(word.text for word in words),
                confidence=0.9,
                audio_window_start_seconds=chunk.start_seconds,
                audio_window_end_seconds=chunk.end_seconds,
                words=words,
            )


def _spoken_seconds(cues: Iterable[CaptionCue]) -> set[int]:
    """Absolute audio seconds that reached the ACTIVE (on-air) caption set."""

    return {
        int(token[1:])
        for cue in cues
        for token in cue.text.split()
        if token.startswith("s") and token[1:].isdigit()
    }


class TestCaptionTapCatchUp:
    """U23: a stalled channel CATCHES UP; it does not go quiet for minutes.

    U22 OBSERVED the live failure on the station: single ASR windows of
    10.6-19.5 s against a 5 s audio cadence, often on two or three channels
    within milliseconds of each other (one shared GPU), on a box whose MEAN
    throughput was still 1.86x the cadence. A ~16 s stall therefore tripped the
    tap's persistence gate and PAUSED three channels for 120 s at a time, 240 s
    and 480 s after the ladder escalated -- 19 overloads in 67.5 minutes of live
    audio where captions simply stopped. The acceptance verifier samples those
    minutes as "no embedded cues" and fails the station.

    The decision under test: when a channel's overshoot PERSISTS for the whole
    U11 window, keep captioning from the NEWEST audio and discard only the oldest
    settled segments that can no longer be captioned in time. The channel never
    stops for longer than the backlog it sheds. The exponential pause ladder is
    retained, unchanged, for the case this cannot fix.
    """

    def test_a_stall_on_three_channels_sheds_and_keeps_captioning(self, tmp_path: Path) -> None:
        """The measured failure itself: 3 channels, ~15 s of stalled audio each.

        The contract this asserts, in one scan: no pause, no cleared captions,
        ASR resumed on the newest audio, the discarded audio airs nothing, and
        the window handed to ASR after the shed is contiguous -- its OWN
        segment, never a join across the seconds that were thrown away.
        """

        tap_root = tmp_path / "tap"
        segment_seconds = 5.0
        runtime = _TimedToneRuntime()
        clock = _FakeClock()
        worker = _worker(
            tap_root,
            runtime,
            InMemoryCaptionReviewStore(),
            segment_seconds=segment_seconds,
            max_channel_workers=3,
            monotonic=clock,
        )
        next_index = dict.fromkeys(_CATCH_UP_CHANNELS, 0)

        def arrivals(channel: str, count: int) -> None:
            for _ in range(count):
                _write_wav(
                    tap_root / channel / f"chunk-{next_index[channel]:06d}.wav",
                    seconds=segment_seconds,
                )
                next_index[channel] += 1

        # The persistence window: every scan is over the limit AND the tap keeps
        # up -- each scan transcribes the two oldest settled segments and leaves
        # the rest queued. That is the U10 shape the gate keys on: a sustained
        # overshoot on a box whose ASR pool is working, not idle.
        for scan in range(_OVERLOAD_PERSISTENCE_WINDOW - 1):
            for channel in _CATCH_UP_CHANNELS:
                arrivals(channel, 4)
            window_scan = worker.run_once()
            assert window_scan.paused_channels == (), f"scan {scan} paused a channel"
            assert window_scan.overloaded_channels == (), f"scan {scan} paused a channel"
            assert window_scan.dropped_overload_segments == 0, (
                f"scan {scan} deleted live audio inside the persistence window"
            )
        assert runtime.seen_chunks, "no ASR work happened during the window"

        # The stall. Clear the pile those scans accumulated so the acting scan
        # sees exactly the stalled backlog: six fresh segments per channel, of
        # which the newest is held back by the settle guard and the oldest three
        # can no longer be captioned in time.
        for path in tap_root.glob("*/chunk-*.wav"):
            path.unlink()
        transcribed_before = len(runtime.seen_chunks)
        shed_indices: dict[str, range] = {}
        resumed_at: dict[str, int] = {}
        for channel in _CATCH_UP_CHANNELS:
            arrivals(channel, 6)
            settled = list(range(next_index[channel] - 6, next_index[channel] - 1))
            shed = settled[:-2]
            assert len(shed) == 3, shed
            shed_indices[channel] = range(shed[0], shed[-1] + 1)
            resumed_at[channel] = settled[-2]

        acting = worker.run_once()

        assert acting.paused_channels == (), (
            "a transient GPU stall paused the channel: that is the U23 defect"
        )
        assert acting.overloaded_channels == ()
        # Reported, never silent: three channels x three shed segments.
        assert acting.dropped_overload_segments == 3 * 3, acting
        # ...and captioning RESUMED on the newest audio, in the same scan.
        assert acting.consumed_segments == 2 * len(_CATCH_UP_CHANNELS), acting
        # Discarded means DISCARDED, not filed: the shed audio is gone from disk,
        # including out of processed/, collision/ and quarantine/ (there is no
        # ``overload/`` holding pen to check any more -- that directory was
        # retired as an unbounded disk leak, so asserting its absence would pass
        # vacuously on every path). The kept segments, by contrast, are gone
        # because they were really transcribed, which the ASR windows below also
        # prove by name.
        for channel in _CATCH_UP_CHANNELS:
            for index in shed_indices[channel]:
                leftovers = sorted((tap_root / channel).rglob(f"chunk-{index:06d}.wav"))
                assert not leftovers, f"{channel}: discarded audio is still on disk: {leftovers}"
            for index in (resumed_at[channel], resumed_at[channel] + 1):
                assert not (tap_root / channel / f"chunk-{index:06d}.wav").exists(), (
                    f"{channel}: kept segment {index} is still queued -- it was not transcribed"
                )
        for channel in _CATCH_UP_CHANNELS:
            status = _runtime_status(tap_root, channel)
            assert status["state"] == "within-capacity", status
            assert status.get("consecutive_overloads", 0) == 0, status
            assert status["backlog_segments"] <= status["max_backlog_segments"], status

        # THE SEAM. A window handed to ASR must be contiguous audio, so the first
        # window after a shed is its OWN segment: one cadence of audio starting
        # exactly at its own index -- not 5 s earlier, which is the geometry a
        # join across the discarded gap would carry.
        new_chunks = runtime.seen_chunks[transcribed_before:]
        assert len(new_chunks) == 2 * len(_CATCH_UP_CHANNELS), new_chunks
        for channel in _CATCH_UP_CHANNELS:
            windows = [
                chunk for chunk in new_chunks if chunk.chunk_id.startswith(f"{channel}-tap-")
            ]
            assert len(windows) == 2, [chunk.chunk_id for chunk in windows]
            head, tail = windows
            first = resumed_at[channel]
            assert head.chunk_id == f"{channel}-tap-{first:06d}", head.chunk_id
            assert head.start_seconds == first * segment_seconds, head.start_seconds
            assert len(head.pcm_s16le) == 2 * int(TAP_SAMPLE_RATE_HZ * segment_seconds), (
                "the resume window carried more audio than its own segment: the "
                "seam joined across the discarded seconds"
            )
            # The break is exactly one window wide: the next kept segment still
            # joins its own immediate predecessor, so overlap resumes at once.
            assert tail.chunk_id == f"{channel}-tap-{first + 1:06d}-overlap", tail.chunk_id
            assert tail.start_seconds == first * segment_seconds, tail.start_seconds
            assert tail.end_seconds - tail.start_seconds == 2 * segment_seconds, tail

        for channel in _CATCH_UP_CHANNELS:
            cues = load_caption_cues_from_timed_text(
                _active_vtt(tap_root, channel), source_id=channel
            )
            spoken = _spoken_seconds(cues)
            discarded = {
                second
                for index in shed_indices[channel]
                for second in range(
                    int(index * segment_seconds), int((index + 1) * segment_seconds)
                )
            }
            assert discarded, shed_indices[channel]
            assert not (spoken & discarded), (
                f"{channel}: a caption aired audio from a discarded second "
                f"({sorted(spoken & discarded)}); the seam joined across the shed"
            )
            gap_start = shed_indices[channel].start * segment_seconds
            gap_end = shed_indices[channel].stop * segment_seconds
            for cue in cues:
                assert cue.end_seconds <= gap_start or cue.start_seconds >= gap_end, (
                    f"{channel}: cue {cue.text!r} spans the discarded gap "
                    f"[{gap_start}, {gap_end}): ({cue.start_seconds}, {cue.end_seconds})"
                )
            # Neither side of the gap may be vacuous: the channel really did
            # caption before the stall, and really did resume after it.
            assert any(second < gap_start for second in spoken), (
                f"{channel}: nothing before the gap aired, so the assertions above "
                f"could pass vacuously (spoken={sorted(spoken)})"
            )
            assert any(second >= gap_end for second in spoken), (
                f"{channel}: captions did not resume after the shed (spoken={sorted(spoken)})"
            )

    def test_a_shed_is_recorded_in_the_batch_diagnostic(self, tmp_path: Path) -> None:
        """Catch-up must not bypass the U18 per-batch record: a shed IS a drop.

        The diagnostic is how an operator explains missing captions after the
        fact, so a catch-up shed has to appear there with its own reason, the
        segments actually thrown away, and the depth the gate saw. This is also
        what keeps the diagnostic contract covered once the four tests in
        ``test_caption_tap_batch_diagnostic.py`` pin the FAIL-CLOSED record with
        catch-up explicitly switched off.
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        collector = tbd.BatchDiagnosticCollector()
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            atomic_segments=True,
            overload_persistence_scans=1,
            batch_diagnostic=collector,
        )
        worker._sweep_retention()
        assert worker.wait_for_retention_sweep(timeout=5.0)

        # Written AFTER construction: audio already on disk at construction
        # belongs to a previous session and is discarded at startup (U11 B1).
        for index in range(4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav")

        result = worker.run_once()

        assert result.paused_channels == ()
        assert result.dropped_overload_segments == 2, result
        records = [
            record
            for record in collector._records
            if record.get("reason") == "max-backlog-catch-up-shed"
        ]
        assert len(records) == 1, collector._records
        record = records[0]
        assert record["channel"] == channel
        assert record["outcome"] == "overloaded"
        assert record["queue_depth"] == 4
        assert record["segment_indices"] == [0, 1]

    def test_sustained_under_capacity_still_earns_the_retained_pause(self, tmp_path: Path) -> None:
        """The ladder is retained for a box that sheds and STILL cannot cope.

        One shed means "a stall happened". Shedding on every persistence window
        for minutes means the box cannot transcribe this channel at all, and that
        is the case the pause exists for (playout is the product). With a budget
        of one this drives the escalation boundary exactly: the first sustained
        episode sheds, the second pauses at the shipped base rung, and the budget
        is then reset so a released channel is judged afresh.
        """

        tap_root = tmp_path / "tap"
        channel = "government"
        clock = _FakeClock()
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            catch_up_shed_limit=1,
            monotonic=clock,
            backoff_policy=CaptionBackoffPolicy(monotonic=clock),
        )

        shed_scan, next_index = _drive_sustained_episode(worker, tap_root, channel)
        assert shed_scan.paused_channels == (), "the first sustained overshoot paused the channel"
        assert shed_scan.dropped_overload_segments > 0
        assert shed_scan.consumed_segments > 0
        assert _runtime_status(tap_root, channel)["state"] == "within-capacity"

        paused_scan, next_index = _drive_sustained_episode(
            worker, tap_root, channel, first_index=next_index
        )
        assert paused_scan.overloaded_channels == (channel,)
        assert paused_scan.paused_channels == (channel,)
        status = _runtime_status(tap_root, channel)
        assert status["state"] == "paused"
        assert status["consecutive_overloads"] == 1
        assert status["resume_in_seconds"] == DEFAULT_BASE_BACKOFF_SECONDS
        assert load_caption_cues_from_timed_text(_active_vtt(tap_root, channel)) == []

        # The ladder took over, so the shed budget was cleared with it: a
        # released channel is judged afresh rather than escalating instantly.
        clock.advance(DEFAULT_BASE_BACKOFF_SECONDS + 1.0)
        released, _ = _drive_sustained_episode(worker, tap_root, channel, first_index=next_index)
        assert released.paused_channels == (), (
            "the shed budget survived the pause: a released channel escalated on its first new shed"
        )
        assert released.dropped_overload_segments > 0

    def test_a_single_over_limit_scan_spends_no_shed_budget(self, tmp_path: Path) -> None:
        """The U11 contract survives, and it costs nothing from the budget.

        U10 measured 128 of 182 live trips firing at exactly one segment over the
        limit against a max of 2. A single over-limit scan must neither pause a
        channel (U11) nor count against catch-up, so the budget of one below is
        still unspent for the sustained episode that follows.
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            catch_up_shed_limit=1,
        )
        for index in range(4):
            _write_wav(tap_root / channel / f"chunk-{index:06d}.wav", seconds=5.0)

        single = worker.run_once()

        assert single.paused_channels == ()
        assert single.overloaded_channels == ()
        assert single.dropped_overload_segments == 0, (
            "a single over-limit scan deleted live audio instead of transcribing it"
        )
        assert single.consumed_segments == 2
        assert _runtime_status(tap_root, channel)["state"] == "within-capacity"

        # The station catches up, so the over-limit streak clears -- and nothing
        # about the single overshoot is remembered.
        for path in (tap_root / channel).glob("chunk-*.wav"):
            path.unlink()
        assert worker.run_once().dropped_overload_segments == 0

        shed_scan, _ = _drive_sustained_episode(worker, tap_root, channel, first_index=4)
        assert shed_scan.paused_channels == (), (
            "the single over-limit scan had already spent the channel's shed budget"
        )
        assert shed_scan.dropped_overload_segments > 0

    def test_a_shed_is_not_a_session_change_and_the_pause_still_invalidates(
        self, tmp_path: Path
    ) -> None:
        """Catch-up must not black out a live channel -- nor break the guard.

        A shed is not a session boundary: nothing is cleared, the generation does
        not move, and a result still in flight from this broadcast may still
        publish. The retained PAUSE keeps the opposite behaviour, unchanged: it
        invalidates in-flight work so a stale result cannot repaint captions the
        pause has cleared.
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            catch_up_shed_limit=1,
        )
        worker.begin_channel_session(channel)
        generation = worker._session_generation[channel]

        shed_scan, next_index = _drive_sustained_episode(worker, tap_root, channel)
        assert shed_scan.paused_channels == ()
        assert shed_scan.dropped_overload_segments > 0
        assert worker._session_generation[channel] == generation, (
            "the shed bumped the session generation: catch-up is not a new session "
            "and must not invalidate a live channel's own in-flight results"
        )

        current = [
            CaptionCue(
                cue_id="cue-CURRENT",
                start_seconds=100.0,
                end_seconds=104.0,
                text="CURRENT SESSION CAPTION",
                confidence=0.9,
                low_confidence=False,
            )
        ]
        assert worker.publish_for_current_session(channel, generation, current) is True
        assert "CURRENT SESSION CAPTION" in _active_vtt(tap_root, channel).read_text(
            encoding="utf-8"
        )

        paused_scan, _ = _drive_sustained_episode(worker, tap_root, channel, first_index=next_index)
        assert paused_scan.overloaded_channels == (channel,)
        assert worker._session_generation[channel] > generation, (
            "the fail-closed pause no longer invalidates the channel's in-flight work"
        )

        stale = [
            CaptionCue(
                cue_id="cue-STALE",
                start_seconds=100.0,
                end_seconds=104.0,
                text="STALE IN FLIGHT CAPTION",
                confidence=0.9,
                low_confidence=False,
            )
        ]
        assert worker.publish_for_current_session(channel, generation, stale) is False
        after = _active_vtt(tap_root, channel).read_text(encoding="utf-8")
        assert "STALE IN FLIGHT CAPTION" not in after, (
            "a pre-pause in-flight result repainted captions the pause had cleared"
        )
        assert "CURRENT SESSION CAPTION" not in after, "the pause did not clear the live captions"

    def test_the_shed_budget_renews_after_its_window(self, tmp_path: Path) -> None:
        """The budget is a RATE, not a lifetime allowance.

        A station that sheds once and then keeps up for the rest of the window
        has earned another shed. It has to: otherwise a single GPU stall early in
        the shift would leave every later stall unprotected until the process
        restarted.
        """

        tap_root = tmp_path / "tap"
        channel = "education"
        clock = _FakeClock()
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            catch_up_shed_limit=1,
            catch_up_shed_window_seconds=60.0,
            monotonic=clock,
        )

        first, next_index = _drive_sustained_episode(worker, tap_root, channel)
        assert first.paused_channels == (), "the first sustained overshoot paused the channel"
        assert first.dropped_overload_segments > 0

        clock.advance(61.0)
        second, next_index = _drive_sustained_episode(
            worker, tap_root, channel, first_index=next_index
        )
        assert second.paused_channels == (), (
            "a shed older than the shed window still counted against the channel"
        )
        assert second.dropped_overload_segments > 0

        # ...and the budget really is a budget: the shed inside the window now
        # counts, so a third episode with no time passing pauses instead.
        third, _ = _drive_sustained_episode(worker, tap_root, channel, first_index=next_index)
        assert third.overloaded_channels == (channel,)

    def test_the_catch_up_warning_names_what_an_operator_has_to_correlate(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ONE WARNING per episode, and it is not the pause line.

        An operator has to be able to correlate a gap in the captions with the
        log: which channel, how many segments, how many seconds of audio, which
        indices, and that captions are still running. A grep for a pause must not
        match it -- a working tap must not look like a broken one.
        """

        tap_root = tmp_path / "tap"
        channel = "government"
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            segment_seconds=5.0,
            catch_up_shed_limit=1,
        )

        with caplog.at_level(logging.DEBUG, logger="civiccast.captions.tap_worker"):
            result, _ = _drive_sustained_episode(
                worker, tap_root, channel, per_scan=4, final_files=6
            )

        assert result.paused_channels == ()
        assert result.dropped_overload_segments == 3
        shed_lines = [record for record in caplog.records if "catch-up" in record.getMessage()]
        assert len(shed_lines) == 1, [record.getMessage() for record in shed_lines]
        assert shed_lines[0].levelno == logging.WARNING
        message = shed_lines[0].getMessage()
        # 14 scans x 4 files, so the acting scan sees indices 56-61: 56, 57 and
        # 58 are what it sheds.
        assert channel in message, message
        assert "3 segment(s)" in message, message
        assert "indices 56-58" in message, message
        # The seconds reported are the audio ACTUALLY discarded, read from each
        # WAV's own header -- not the nominal cadence multiplied by the count.
        # This worker is deliberately configured with a 5s cadence while the
        # helper writes 1s WAVs, so a report that trusted `segment_seconds` would
        # say 15.0s here and this assertion is what would catch it.
        assert "3.0s of audio" in message, message
        assert "15.0s of audio" not in message, message
        assert not [
            record for record in caplog.records if "Caption tap overload" in record.getMessage()
        ]
        assert not [record for record in caplog.records if "PAUSED" in record.getMessage()]

    def test_catch_up_can_be_switched_off_with_a_zero_shed_limit(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``0`` is the documented escape hatch back to the pre-U23 rule.

        Not ``1``: a limit of one still sheds once before the second over-limit
        episode trips the ladder. Zero is the only value that restores "a
        persistent overshoot pauses, and nothing is shed".
        """

        tap_root = tmp_path / "tap"
        channel = "public"
        worker = _worker(
            tap_root,
            _ScriptedRuntime(),
            InMemoryCaptionReviewStore(),
            catch_up_shed_limit=0,
        )

        with caplog.at_level(logging.DEBUG, logger="civiccast.captions.tap_worker"):
            result, _ = _drive_sustained_episode(worker, tap_root, channel)

        assert result.overloaded_channels == (channel,)
        assert result.paused_channels == (channel,)
        assert _runtime_status(tap_root, channel)["state"] == "paused"
        assert not [record for record in caplog.records if "catch-up" in record.getMessage()], (
            "catch-up ran with a shed limit of zero"
        )

    def test_the_shipped_catch_up_defaults_and_their_environment_parsing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from civiccast.captions.tap_worker import (
            DEFAULT_CATCH_UP_SHED_LIMIT,
            DEFAULT_CATCH_UP_SHED_WINDOW_SECONDS,
        )

        # The mirrors this test file is written against must equal the shipped
        # defaults, so the copy cannot drift.
        assert DEFAULT_CATCH_UP_SHED_LIMIT == _CATCH_UP_SHED_LIMIT
        assert DEFAULT_CATCH_UP_SHED_WINDOW_SECONDS == _CATCH_UP_SHED_WINDOW_SECONDS

        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_LIMIT", raising=False)
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_WINDOW_SECONDS", raising=False)
        defaults = CaptionTapWorkerSettings.from_env()
        assert defaults.catch_up_shed_limit == DEFAULT_CATCH_UP_SHED_LIMIT
        assert defaults.catch_up_shed_window_seconds == DEFAULT_CATCH_UP_SHED_WINDOW_SECONDS

        # 0 is the escape hatch back to the pre-U23 rule, not a typo.
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_LIMIT", "0")
        assert CaptionTapWorkerSettings.from_env().catch_up_shed_limit == 0
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_LIMIT", "2")
        assert CaptionTapWorkerSettings.from_env().catch_up_shed_limit == 2
        # A NEGATIVE (or non-numeric) limit is nonsense, but this knob is parsed
        # with the CLAMPING parser: it warns and uses the shipped default rather
        # than refusing to start. That split is deliberate -- a station that will
        # not boot over an optional caption-tuning variable is a worse failure
        # than one that logs and carries on. The CONSTRUCTOR still refuses it (the
        # injected-worker assertions below), so an in-process caller cannot get a
        # silently wrong value.
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_LIMIT", "-1")
        assert (
            CaptionTapWorkerSettings.from_env().catch_up_shed_limit == DEFAULT_CATCH_UP_SHED_LIMIT
        )
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_LIMIT", "not-a-number")
        assert (
            CaptionTapWorkerSettings.from_env().catch_up_shed_limit == DEFAULT_CATCH_UP_SHED_LIMIT
        )

        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_WINDOW_SECONDS", "90")
        assert CaptionTapWorkerSettings.from_env().catch_up_shed_window_seconds == 90.0
        # A zero-length window would mean "no shed ever counts as recent", so it
        # is clamped to the shipped default as well: accepted, defaulted, warned.
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_WINDOW_SECONDS", "0")
        assert (
            CaptionTapWorkerSettings.from_env().catch_up_shed_window_seconds
            == DEFAULT_CATCH_UP_SHED_WINDOW_SECONDS
        )

        # The constructor validates the same two knobs for an injected worker.
        with pytest.raises(ValueError, match="catch_up_shed_limit"):
            _worker(
                tmp_path / "tap",
                _ScriptedRuntime(),
                InMemoryCaptionReviewStore(),
                catch_up_shed_limit=-1,
            )
        with pytest.raises(ValueError, match="catch_up_shed_window_seconds"):
            _worker(
                tmp_path / "tap",
                _ScriptedRuntime(),
                InMemoryCaptionReviewStore(),
                catch_up_shed_window_seconds=0.0,
            )


def test_caption_warmup_respects_live_switch_and_cancellation(tmp_path):
    from unittest.mock import Mock

    runtime = _ScriptedRuntime()
    runtime.warm_channel = Mock()
    enabled = [False]
    worker = _worker(
        tmp_path / "tap", runtime, InMemoryCaptionReviewStore(), is_enabled=lambda: enabled[0]
    )
    cancel = threading.Event()
    worker.prepare_channel_runtime("gov", cancel)
    runtime.warm_channel.assert_not_called()
    enabled[0] = True
    cancel.set()
    worker.prepare_channel_runtime("gov", cancel)
    runtime.warm_channel.assert_not_called()
    cancel.clear()
    worker.prepare_channel_runtime("gov", cancel)
    runtime.warm_channel.assert_called_once_with("gov", cancel)
