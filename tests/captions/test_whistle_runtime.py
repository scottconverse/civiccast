# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors


import inspect

import pytest

from civiccast.captions import whistle
from civiccast.captions.models import AudioChunk
from civiccast.captions.whistle import WhistleRuntime


def test_speech_child_starts_at_normal_priority():
    """FT-7 (Beelink, 2026-11-06): the live caption speech child starts at
    NORMAL (0x20), above the BELOW_NORMAL air-path preparation and the IDLE
    background preparation, still below the ABOVE_NORMAL playout encoders. At
    BELOW_NORMAL it lost to the air-path encode at every programme change."""
    assert whistle.SPEECH_CHILD_PRIORITY_CLASS == 0x20
    source = inspect.getsource(whistle._SpeechProcess.__init__)
    assert "creationflags=0x4 | 0x08000000 | SPEECH_CHILD_PRIORITY_CLASS" in source
    assert "0x4000" not in source


def chunk(channel="public", index=1):
    return AudioChunk(
        chunk_id=f"{channel}-tap-{index:06d}",
        start_seconds=100,
        end_seconds=110,
        sample_rate_hz=16000,
        pcm_s16le=b"\0" * 320000,
    )


class Worker:
    def __init__(self, engine, calls, fail=False):
        self.engine, self.calls, self.fail = engine, calls, fail
        self.closed = False

    def request(self, audio, vocabulary):
        self.calls.append((self.engine, audio.chunk_id, audio.pcm_s16le, vocabulary))
        if self.fail:
            raise TimeoutError("blocked native call")
        if self.engine == "whisper":
            return [
                {
                    "source_id": audio.chunk_id,
                    "start_seconds": 100.5,
                    "end_seconds": 102,
                    "text": "fallback words",
                    "audio_window_start_seconds": 100,
                    "audio_window_end_seconds": 110,
                }
            ]
        return {
            "text": "five to two",
            "words": [
                {"word": "five", "start": 0.5, "end": 1, "probability": 0.9},
                {"word": "to", "start": 1, "end": 1.2, "probability": 0.9},
                {"word": "two", "start": 1.2, "end": 2, "probability": 0.9},
            ],
        }

    def close(self):
        self.closed = True


def runtime(tmp_path, *, fail=False, response=None):
    calls, workers = [], []

    def factory(engine):
        worker = Worker(engine, calls, fail and engine == "whistle")
        if response is not None and engine == "whistle":
            worker.request = lambda *_: response
        workers.append(worker)
        return worker

    return (
        WhistleRuntime(
            weights=tmp_path / "whistle.cact",
            library=tmp_path / "libneedle.dll",
            fallback_config={},
            worker_factory=factory,
        ),
        calls,
        workers,
    )


def test_word_times_become_absolute_without_losing_audio_provenance(tmp_path):
    r, _calls, _ = runtime(tmp_path)
    result = list(r.transcribe([chunk()]))
    assert len(result) == 1
    assert result[0].text == "five to two"
    assert result[0].words[0].start_seconds == 100.5
    assert result[0].words[-1].end_seconds == 102
    assert result[0].audio_window_start_seconds == 100
    assert result[0].audio_window_end_seconds == 110


def test_failure_replays_exact_audio_once_then_stays_on_fallback(tmp_path):
    r, calls, workers = runtime(tmp_path, fail=True)
    first = list(r.transcribe([chunk()]))
    second = list(r.transcribe([chunk(index=2)]))
    assert first[0].text == second[0].text == "fallback words"
    assert [c[0] for c in calls] == ["whistle", "whisper", "whisper"]
    assert calls[0][2] == calls[1][2]
    assert workers[0].closed


def test_provider_status_reports_primary_then_fallback(tmp_path):
    r, _calls, _workers = runtime(tmp_path, fail=True)
    status = getattr(r, "live_provider_status", lambda _channel: ("missing", None))
    assert status("public") == ("whistle-primary", None)
    list(r.transcribe([chunk()]))
    assert status("public")[0] == "whistle-recovery-cooldown"


def test_recovery_probe_promotes_primary_without_publishing_probe_result(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    import civiccast.captions.whistle as whistle_module

    now = [100.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls, workers = [], []
    probe_started, finish_probe, probe_finished = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    whistle_creations = 0

    def factory(engine):
        nonlocal whistle_creations
        if engine == "whistle":
            whistle_creations += 1
            worker = Worker(engine, calls, fail=whistle_creations == 1)
            if whistle_creations == 2:
                original_request = worker.request

                def probe(audio, vocabulary):
                    probe_started.set()
                    assert finish_probe.wait(3)
                    try:
                        return original_request(audio, vocabulary)
                    finally:
                        probe_finished.set()

                worker.request = probe
        else:
            worker = Worker(engine, calls)
        workers.append(worker)
        return worker

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    try:
        results = list(r.transcribe([chunk(index=1)]))
        assert results[0].text == "fallback words"
        now[0] += 30

        # The current chunk stays on Whisper while the background probe runs.
        fallback_result = list(r.transcribe([chunk(index=2)]))
        assert fallback_result[0].text == "fallback words"
        assert probe_started.wait(2), "recovery probe did not start after cooldown"
        assert r.live_provider_status("public") == ("whistle-recovering", None)

        finish_probe.set()
        assert probe_finished.wait(2), "recovery probe did not finish"
        assert r._recovery_thread is not None
        r._recovery_thread.join(timeout=2)
        assert not r._recovery_thread.is_alive()
        assert r.live_provider_status("public") == ("whistle-primary", None)

        # Probe output is discarded; only a later chunk is sent to primary.
        results = list(r.transcribe([chunk(index=3)]))
        assert results[0].text == "five to two"
        assert [call[1] for call in calls if call[0] == "whistle"] == [
            "public-tap-000001",
            "public-tap-000002",
            "public-tap-000003",
        ]
        assert len(fallback_result) == 1
    finally:
        finish_probe.set()
        r.close()


def test_valid_silence_recovery_probe_promotes_primary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import civiccast.captions.whistle as whistle_module

    now = [0.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls, workers = [], []
    whistle_creations = 0

    def factory(engine):
        nonlocal whistle_creations
        if engine == "whistle":
            whistle_creations += 1
            worker = Worker(engine, calls, fail=whistle_creations == 1)
            if whistle_creations == 2:
                original_request = worker.request

                def silence_probe(audio, vocabulary):
                    result = original_request(audio, vocabulary)
                    if audio.chunk_id == "public-tap-000002":
                        return {"text": "", "words": []}
                    return result

                worker.request = silence_probe
        else:
            worker = Worker(engine, calls)
        workers.append(worker)
        return worker

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    results = list(r.transcribe([chunk(index=1)]))
    assert results[0].text == "fallback words"
    now[0] = 30
    results = list(r.transcribe([chunk(index=2)]))
    assert results[0].text == "fallback words"
    assert r._recovery_thread is not None
    r._recovery_thread.join(timeout=2)
    assert not r._recovery_thread.is_alive()
    assert r.live_provider_status("public") == ("whistle-primary", None)
    results = list(r.transcribe([chunk(index=3)]))
    assert results[0].text == "five to two"
    assert [call[0] for call in calls if call[0] == "whistle"] == [
        "whistle",
        "whistle",
        "whistle",
    ]
    r.close()
    assert all(worker.closed for worker in workers)


def test_failed_recovery_probe_uses_exponential_backoff_capped_at_five_minutes(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    import civiccast.captions.whistle as whistle_module

    now = [100.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls, workers = [], []

    def factory(engine):
        worker = Worker(engine, calls, fail=engine == "whistle")
        workers.append(worker)
        return worker

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    results = list(r.transcribe([chunk(index=1)]))
    assert results[0].text == "fallback words"
    assert r._recoveries["public"].next_attempt_at == 130.0

    for index, retry_delay in enumerate((60, 120, 240, 300, 300), start=2):
        now[0] = r._recoveries["public"].next_attempt_at
        results = list(r.transcribe([chunk(index=index)]))
        assert results[0].text == "fallback words"
        assert r._recovery_thread is not None
        r._recovery_thread.join(timeout=2)
        assert not r._recovery_thread.is_alive()
        recovery = r._recoveries["public"]
        assert recovery.next_attempt_at - now[0] == retry_delay

    r.close()
    assert all(worker.closed for worker in workers)


def test_only_one_channel_recovery_probe_runs_stationwide(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    import civiccast.captions.whistle as whistle_module

    now = [0.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls, workers = [], []
    probe_started, release_probe = threading.Event(), threading.Event()
    whistle_creations = 0

    def factory(engine):
        nonlocal whistle_creations
        if engine == "whistle":
            whistle_creations += 1
            worker = Worker(engine, calls, fail=whistle_creations <= 2)
            if whistle_creations == 3:
                original_request = worker.request

                def blocked_probe(audio, vocabulary):
                    probe_started.set()
                    assert release_probe.wait(3)
                    return original_request(audio, vocabulary)

                worker.request = blocked_probe
        else:
            worker = Worker(engine, calls)
        workers.append(worker)
        return worker

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    try:
        results = list(r.transcribe([chunk("public", 1)]))
        assert results[0].text == "fallback words"
        results = list(r.transcribe([chunk("government", 1)]))
        assert results[0].text == "fallback words"
        now[0] = 30
        results = list(r.transcribe([chunk("public", 2)]))
        assert results[0].text == "fallback words"
        assert probe_started.wait(2)
        results = list(r.transcribe([chunk("government", 2)]))
        assert results[0].text == "fallback words"
        assert whistle_creations == 3
    finally:
        release_probe.set()
        thread = r._recovery_thread
        if thread is not None:
            thread.join(timeout=2)
        r.close()


def test_close_during_recovery_cancels_late_promotion_and_closes_probe(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    import civiccast.captions.whistle as whistle_module

    now = [0.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls, workers = [], []
    probe_started, release_probe = threading.Event(), threading.Event()
    whistle_creations = 0

    def factory(engine):
        nonlocal whistle_creations
        if engine == "whistle":
            whistle_creations += 1
            worker = Worker(engine, calls, fail=whistle_creations == 1)
            if whistle_creations == 2:
                original_request = worker.request

                def blocked_probe(audio, vocabulary):
                    probe_started.set()
                    assert release_probe.wait(3)
                    return original_request(audio, vocabulary)

                worker.request = blocked_probe
                original_close = worker.close

                def close_probe():
                    original_close()
                    release_probe.set()

                worker.close = close_probe
        else:
            worker = Worker(engine, calls)
        workers.append(worker)
        return worker

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    results = list(r.transcribe([chunk(index=1)]))
    assert results[0].text == "fallback words"
    now[0] = 30
    results = list(r.transcribe([chunk(index=2)]))
    assert results[0].text == "fallback words"
    assert probe_started.wait(2)
    probe = workers[-1]
    r.close()
    assert probe.closed
    assert r._recovery_thread is not None
    r._recovery_thread.join(timeout=2)
    assert not r._recovery_thread.is_alive()
    assert "public" not in r._primaries
    assert not r._recoveries


def test_provider_status_does_not_wait_for_fallback_initialization_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    r, _calls, _workers = runtime(tmp_path)
    with r._guard, ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(r.live_provider_status, "public").result(timeout=2) == (
            "unknown",
            None,
        )
    r.close()


def test_a_failed_channel_does_not_disable_another_channel(tmp_path):
    r, calls, _workers = runtime(tmp_path, fail=True)
    list(r.transcribe([chunk()]))
    list(r.transcribe([chunk("government")]))
    assert [c[0] for c in calls] == ["whistle", "whisper", "whistle", "whisper"]


def test_silence_returns_no_hypotheses_or_fallback(tmp_path):
    r, _calls, _ = runtime(tmp_path, response={"text": "", "words": []})
    assert list(r.transcribe([chunk()])) == []


def test_invalid_native_word_time_triggers_fallback(tmp_path):
    r, calls, _ = runtime(
        tmp_path,
        response={
            "text": "bad",
            "words": [{"word": "bad", "start": 0, "end": 100, "probability": 1}],
        },
    )
    assert next(iter(r.transcribe([chunk()]))).text == "fallback words"
    assert calls[-1][0] == "whisper"


def test_close_reaps_all_created_workers(tmp_path):
    r, _, workers = runtime(tmp_path)
    list(r.transcribe([chunk()]))
    list(r.transcribe([chunk("government")]))
    r.close()
    assert workers and all(w.closed for w in workers)


def test_isolated_primary_allows_three_channel_dispatch(tmp_path):
    from civiccast.captions.tap_worker import default_max_channel_workers

    r, _, _ = runtime(tmp_path)
    assert not r.on_cuda()
    assert default_max_channel_workers(r) == 3


def test_native_live_factory_selects_whistle_but_batch_stays_whisper(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from civiccast.ai_models.runtime import build_caption_runtime
    from civiccast.captions.runtime import FasterWhisperRuntime
    from civiccast.native import station_runtime
    from civiccast.native.caption_tiers import FLOOR_TIER_ID, CaptionTierSpec

    station_root = tmp_path / "installed-native-root"
    model_root = station_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    model_root.mkdir(parents=True)
    fixture_bodies = {
        "config.json": b"floor-config",
        "model.bin": b"floor-model-bytes",
        "tokenizer.json": b"floor-tokenizer",
        "vocabulary.txt": b"floor-vocab",
    }
    fixture_files = {}
    for name, body in fixture_bodies.items():
        (model_root / name).write_bytes(body)
        fixture_files[name] = (len(body), hashlib.sha256(body).hexdigest())
    floor_registry = {
        **station_runtime.CAPTION_TIER_REGISTRY,
        FLOOR_TIER_ID: CaptionTierSpec(
            tier_id=FLOOR_TIER_ID,
            model_directory="faster-whisper-medium",
            model_repository="Systran/faster-whisper-medium",
            model_revision="test-revision",
            files=fixture_files,
            pending=False,
        ),
    }
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", floor_registry)
    monkeypatch.setenv("CIVICCAST_NATIVE_STATION", "1")
    monkeypatch.setenv("CIVICCAST_WHISTLE_ROOT", str(tmp_path))
    monkeypatch.setenv("CIVICCAST_NATIVE_STATION_ROOT", str(station_root))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "program-data"))
    monkeypatch.delenv("CIVICCAST_CAPTION_TIER", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_MODEL_PATH", raising=False)
    monkeypatch.delenv("CIVICCAST_LIVE_CAPTION_ENGINE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISTLE_CHANNELS", raising=False)
    # Exercise the factory seam without loading Whisper weights. The production
    # resolver still verifies every file in this small synthetic floor pack.
    constructor_args = []

    def fake_whisper_init(self, **kwargs):
        constructor_args.append(kwargs.copy())
        self.__dict__.update(
            model_size_or_path=kwargs["model_size_or_path"],
            packaged_model_path=kwargs["packaged_model_path"],
            packaged_model_tier=kwargs["packaged_model_tier"],
            device="cpu",
            compute_type="int8",
            cpu_threads=1,
            num_workers=3,
            beam_size=1,
            language="en",
            task="transcribe",
            vad_filter=True,
        )

    monkeypatch.setattr(
        FasterWhisperRuntime,
        "__init__",
        fake_whisper_init,
    )
    service = SimpleNamespace(effective_model_tag=lambda feature: "whisper-medium")
    assert isinstance(build_caption_runtime(service, live=True), WhistleRuntime)
    assert constructor_args[0]["packaged_model_path"] == str(model_root.resolve())
    assert constructor_args[0]["packaged_model_tier"] == FLOOR_TIER_ID
    batch = build_caption_runtime(service)
    assert isinstance(batch, FasterWhisperRuntime)
    assert batch.packaged_model_path == str(model_root.resolve())
    assert batch.packaged_model_tier == FLOOR_TIER_ID
    from civiccast.captions.whistle import MixedCaptionRuntime

    monkeypatch.setenv("CIVICCAST_WHISTLE_CHANNELS", "public")
    mixed = build_caption_runtime(service, live=True)
    assert isinstance(mixed, MixedCaptionRuntime)
    assert mixed._whistle_channels == {"public"}
    assert mixed._whisper.num_workers == 3  # Existing Whisper profile stays intact.
    assert isinstance(build_caption_runtime(service), FasterWhisperRuntime)


def test_unavailable_primary_assets_still_use_lazy_fallback(tmp_path):
    r, calls, workers = runtime(tmp_path)
    r.prepare()
    assert workers == []
    assert r._primary_error is not None
    result = list(r.transcribe([chunk()]))
    assert result[0].text == "fallback words"
    assert [c[0] for c in calls] == ["whisper"]
    r.close()


def test_valid_primary_does_not_require_available_backup(tmp_path, monkeypatch):
    r, attempts, workers = _runtime_with_unavailable_backup(tmp_path, monkeypatch)
    r.prepare()
    assert attempts == []
    assert next(iter(r.transcribe([chunk()]))).text == "five to two"
    assert attempts == ["whistle"]
    assert r.live_provider_status("public") == ("whistle-primary", None)
    r.close()
    assert all(w.closed for w in workers)


def test_primary_failure_still_attempts_and_surfaces_unavailable_backup(tmp_path, monkeypatch):
    import pytest

    r, attempts, workers = _runtime_with_unavailable_backup(
        tmp_path, monkeypatch, fail_primary=True
    )
    r.prepare()
    with pytest.raises(RuntimeError, match="backup unavailable"):
        list(r.transcribe([chunk()]))
    assert attempts == ["whistle", "whisper", "whisper"]
    assert workers[0].closed
    assert r.live_provider_status("public")[0] == "unknown"
    with pytest.raises(RuntimeError, match="cooldown"):
        list(r.transcribe([chunk(index=2)]))
    assert attempts == ["whistle", "whisper", "whisper"]
    r.close()


def test_runtime_primary_and_fallback_errors_redact_local_paths(tmp_path, caplog):
    import pytest

    from civiccast.captions.whistle import WhistleRuntime

    calls = []
    local_path = r"C:\private\audio.wav"

    class FailingWorker:
        def __init__(self, engine):
            self.engine = engine

        def request(self, audio, vocabulary):
            calls.append((self.engine, audio.chunk_id))
            raise RuntimeError(local_path)

        def close(self):
            return None

    r = WhistleRuntime(
        weights=tmp_path / "w",
        library=tmp_path / "l",
        fallback_config={},
        worker_factory=FailingWorker,
    )
    with pytest.raises(RuntimeError):
        list(r.transcribe([chunk()]))
    assert [engine for engine, _chunk_id in calls] == ["whistle", "whisper", "whisper"]
    assert local_path not in caplog.text
    assert "switching to Whisper: <path>" in caplog.text
    assert "message=<path>" in caplog.text
    r.close()


def _runtime_with_unavailable_backup(tmp_path, monkeypatch, *, fail_primary=False):
    import hashlib

    import civiccast.captions.whistle as whistle

    for name, constant in (
        ("whistle.cact", "WEIGHTS_SHA256"),
        ("libneedle.dll", "LIBRARY_SHA256"),
    ):
        data = name.encode()
        (tmp_path / name).write_bytes(data)
        monkeypatch.setattr(whistle, constant, hashlib.sha256(data).hexdigest())
    attempts, workers = [], []

    def factory(engine):
        attempts.append(engine)
        if engine == "whisper":
            raise RuntimeError("backup unavailable")
        worker = Worker(engine, [], fail=fail_primary)
        workers.append(worker)
        return worker

    return (
        WhistleRuntime(
            weights=tmp_path / "whistle.cact",
            library=tmp_path / "libneedle.dll",
            fallback_config={},
            worker_factory=factory,
        ),
        attempts,
        workers,
    )


def test_close_during_primary_request_refuses_late_results(tmp_path):
    import threading

    import pytest

    r, _, workers = runtime(tmp_path)
    list(r.transcribe([chunk()]))
    entered, release = threading.Event(), threading.Event()
    original = workers[0].request

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    workers[0].request = blocked
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: list(r.transcribe([chunk(index=2)])))
        assert entered.wait(3)
        r.close()
        release.set()
        with pytest.raises(RuntimeError, match="closed"):
            future.result(timeout=3)
    assert all(w.closed for w in workers)


def test_failed_fallback_replays_retained_window_to_one_replacement(tmp_path):
    calls, workers = [], []

    def factory(engine):
        fail = engine == "whistle" or not any(w.engine == "whisper" for w in workers)
        w = Worker(engine, calls, fail)
        workers.append(w)
        return w

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    assert next(iter(r.transcribe([chunk()]))).text == "fallback words"
    assert [c[0] for c in calls] == ["whistle", "whisper", "whisper"]
    assert all(c[1:3] == calls[0][1:3] for c in calls)
    r.close()
    assert all(w.closed for w in workers)


def test_failed_fallback_replacement_recovers_after_cooldown(tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace

    import pytest

    import civiccast.captions.whistle as whistle_module

    now = [100.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls, workers = [], []
    whisper_failures = iter((True, True, False, False))

    def factory(engine):
        fail = engine == "whistle" or next(whisper_failures)
        worker = Worker(engine, calls, fail)
        workers.append(worker)
        return worker

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    monkeypatch.setattr(r, "_start_recovery_if_due", lambda *_args: None)

    with pytest.raises(TimeoutError, match="blocked native call"):
        list(r.transcribe([chunk(index=1)]))

    assert [call[0] for call in calls] == ["whistle", "whisper", "whisper"]
    assert workers[0].closed and workers[1].closed

    with pytest.raises(RuntimeError, match="cooldown"):
        list(r.transcribe([chunk(index=2)]))
    assert workers[2].closed
    assert len(workers) == 3

    now[0] += 29.9
    with pytest.raises(RuntimeError, match="cooldown"):
        list(r.transcribe([chunk(index=3)]))
    assert len(workers) == 3

    now[0] += 0.1
    result = list(r.transcribe([chunk(index=4)]))
    assert result[0].text == "fallback words"
    assert workers[-1].engine == "whisper" and not workers[-1].closed
    assert calls[-1][1] == "public-tap-000004"
    assert "channel=public" in caplog.text
    assert "chunk=public-tap-000001" in caplog.text
    assert "phase=immediate-replay" in caplog.text
    assert "exception=TimeoutError" in caplog.text
    assert "message=blocked native call" in caplog.text
    assert "fallback words" not in caplog.text

    workers[-1].fail = True
    recovered_again = list(r.transcribe([chunk(index=5)]))
    assert recovered_again[0].text == "fallback words"
    assert calls[-2][0] == calls[-1][0] == "whisper"
    assert calls[-2][1] == calls[-1][1] == "public-tap-000005"
    assert workers[-2].closed and not workers[-1].closed

    r.close()
    assert all(worker.closed for worker in workers)


def test_shutdown_during_primary_creation_reaps_late_child(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import pytest

    entered, release = threading.Event(), threading.Event()
    workers = []

    def factory(engine):
        w = Worker(engine, [])
        workers.append(w)
        entered.set()
        assert release.wait(3)
        return w

    r = WhistleRuntime(
        weights=tmp_path / "w", library=tmp_path / "l", fallback_config={}, worker_factory=factory
    )
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: list(r.transcribe([chunk()])))
        assert entered.wait(3)
        r.close()
        release.set()
        with pytest.raises(RuntimeError, match="closed"):
            future.result(timeout=3)
    assert all(w.closed for w in workers)


def test_concurrent_child_close_releases_job_once(monkeypatch):
    import sys
    import tempfile
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import ModuleType

    from civiccast.captions.whistle import _SpeechProcess

    class Job:
        closed = 0

        def Close(self):
            self.closed += 1

    job = Job()
    entered, release = threading.Event(), threading.Event()

    def terminate(handle, code):
        assert handle is job
        entered.set()
        assert release.wait(3)

    # This test covers cleanup locking, not pywin32. Keep the native-only API
    # call deterministic on Linux test runners as well as on Windows.
    win32job = ModuleType("win32job")
    win32job.TerminateJobObject = terminate
    monkeypatch.setitem(sys.modules, "win32job", win32job)
    child = object.__new__(_SpeechProcess)
    child._job, child._process, child._closed = job, None, False
    child._close_lock = threading.Lock()
    child._stderr = tempfile.TemporaryFile()  # noqa: SIM115 -- child.close owns this handle
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(child.close)
        assert entered.wait(3)
        b = pool.submit(child.close)
        release.set()
        a.result(timeout=3)
        b.result(timeout=3)
    assert job.closed == 1 and child._stderr.closed


def test_runtime_cleanup_continues_if_one_worker_cleanup_raises(tmp_path):
    r, _, workers = runtime(tmp_path)
    list(r.transcribe([chunk()]))
    list(r.transcribe([chunk("government")]))

    def broken():
        raise OSError("injected cleanup failure")

    workers[0].close = broken
    r.close()
    assert workers[1].closed


def test_audio_send_to_blocked_child_is_deadline_bounded():
    import threading
    from types import SimpleNamespace

    import pytest

    from civiccast.captions.whistle import _SpeechProcess

    released = threading.Event()

    class BlockedInput:
        def write(self, value):
            assert released.wait(3)

        def flush(self):
            pass

    child = object.__new__(_SpeechProcess)
    child._lock = threading.Lock()
    child._closed, child._sequence, child._timeout = False, 0, 0.05
    child._process = SimpleNamespace(stdin=BlockedInput())
    child.close = released.set
    with pytest.raises(TimeoutError, match="input deadline"):
        child.request(chunk(), None)
    assert released.is_set()


@pytest.mark.parametrize("engine,deadline", [("whistle", 30.0), ("whisper", 120.0)])
def test_speech_request_warns_at_ten_seconds_and_contains_a_stalled_child_without_model(
    monkeypatch, caplog, engine, deadline
):
    import queue
    import threading
    from types import SimpleNamespace

    import pytest

    import civiccast.captions.whistle as whistle_module
    from civiccast.captions.whistle import _SpeechProcess

    assert whistle_module._speech_request_timeout("whistle") == 30
    assert whistle_module._speech_request_timeout("whisper") == 120
    now = [0.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class Input:
        def write(self, _value):
            return None

        def flush(self):
            return None

    class SlowThenReady:
        def __init__(self):
            self.calls = []

        def get(self, timeout):
            self.calls.append(timeout)
            if engine == "whisper":
                now[0] += 15
                return {"id": 1, "result": []}
            if len(self.calls) == 1:
                now[0] += timeout
                raise queue.Empty
            now[0] += 0.1
            return {"id": 1, "result": []}

    def bare_child(responses):
        child = object.__new__(_SpeechProcess)
        child._engine = engine
        child._lock = threading.Lock()
        child._closed = False
        child._sequence = 0
        child._timeout = deadline
        child._responses = responses
        child._process = SimpleNamespace(stdin=Input(), pid=None, poll=lambda: None)
        child._stderr_tail = bytearray()
        child._stderr_tail_lock = threading.Lock()
        child.close = lambda: setattr(child, "_closed", True)
        return child

    slow = SlowThenReady()
    assert bare_child(slow).request(chunk(), None) == []
    if engine == "whistle":
        assert slow.calls == [10.0, deadline - 10.0]
        assert "Whistle request is slow" in caplog.text
        assert "phase=response elapsed_s=10.000" in caplog.text
    else:
        assert slow.calls == [120.0]
        assert now[0] == 15.0

    class NeverReady:
        def __init__(self):
            self.calls = []

        def get(self, timeout):
            self.calls.append(timeout)
            now[0] += timeout
            raise queue.Empty

    now[0] = 0.0
    never = NeverReady()
    with pytest.raises(TimeoutError, match="missed its deadline"):
        bare_child(never).request(chunk(), None)
    assert never.calls == ([10.0, deadline - 10.0] if engine == "whistle" else [120.0])


def test_cpu_fallback_deadline_is_configurable_and_finite(monkeypatch):
    import pytest

    from civiccast.captions.whistle import _speech_request_timeout

    monkeypatch.setenv("CIVICCAST_WHISPER_FALLBACK_TIMEOUT_SECONDS", "75")
    assert _speech_request_timeout("whisper") == 75
    assert _speech_request_timeout("whistle") == 30
    for value in ("nan", "inf", "0", "601", "invalid"):
        monkeypatch.setenv("CIVICCAST_WHISPER_FALLBACK_TIMEOUT_SECONDS", value)
        with pytest.raises(ValueError, match="fallback timeout"):
            _speech_request_timeout("whisper")


def test_failed_child_diagnostics_are_bounded_sanitized_and_pre_kill(monkeypatch, caplog):
    import queue
    import sys
    import threading
    from types import ModuleType, SimpleNamespace

    import pytest

    import civiccast.captions.whistle as whistle_module
    from civiccast.captions.whistle import _SpeechProcess

    now = [0.0]
    monkeypatch.setattr(whistle_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    events = []

    class Input:
        def write(self, _value):
            return None

        def flush(self):
            return None

    class NeverReady:
        def get(self, timeout):
            now[0] += timeout
            raise queue.Empty

    class FakeStderr:
        def __init__(self):
            self.buffer = self
            self.chunks = iter([b"x" * 4096 for _ in range(4)] + [b"C:\\private\\native.dll"])

        def read1(self, _size):
            return next(self.chunks, b"")

        def read(self, size):
            return self.read1(size)

    class SnapshotProcess:
        pid = 7654
        stdin = Input()
        returncode = 17

        def poll(self):
            events.append("exit-status")
            return self.returncode

    fake_psutil = ModuleType("psutil")

    class Snapshot:
        def cpu_times(self):
            events.append("cpu")
            return SimpleNamespace(user=0.25, system=0.125)

        def memory_info(self):
            events.append("memory")
            return SimpleNamespace(private=4096)

    def process_snapshot(_pid):
        return Snapshot()

    fake_psutil.Process = process_snapshot
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    child = object.__new__(_SpeechProcess)
    child._engine = "whistle"
    child._lock = threading.Lock()
    child._closed = False
    child._sequence = 0
    child._timeout = 0.05
    child._responses = NeverReady()
    child._process = SnapshotProcess()
    child._stderr = FakeStderr()
    child._stderr_tail = bytearray()
    child._stderr_tail_lock = threading.Lock()
    child._drain_stderr()
    assert len(child._stderr_tail) == whistle_module._STDERR_RING_BYTES

    def terminate_after_snapshot():
        events.append("terminate")
        child._process.returncode = 99
        child._closed = True

    child.close = terminate_after_snapshot
    with pytest.raises(TimeoutError, match="missed its deadline"):
        child.request(chunk(), None)

    assert events == ["exit-status", "cpu", "memory", "terminate"]
    assert "exit_status=17" in caplog.text
    assert "cpu_user_s=0.25" in caplog.text
    assert "private_bytes=4096" in caplog.text
    assert "C:\\private" not in caplog.text


def test_station_serializes_all_three_native_primaries(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    r, _, _ = runtime(tmp_path)
    first_entered, overlapping, release = threading.Event(), threading.Event(), threading.Event()
    count_lock = threading.Lock()
    active = 0

    def factory(engine):
        worker = Worker(engine, [])
        original = worker.request

        def request(audio, vocabulary):
            nonlocal active
            with count_lock:
                active += 1
                if active > 1:
                    overlapping.set()
            first_entered.set()
            try:
                assert release.wait(3)
                return original(audio, vocabulary)
            finally:
                with count_lock:
                    active -= 1

        worker.request = request
        return worker

    r._factory = factory
    with ThreadPoolExecutor(3) as pool:
        futures = [
            pool.submit(lambda c=c: list(r.transcribe([chunk(c)])))
            for c in ("public", "government", "education")
        ]
        assert first_entered.wait(3)
        did_overlap = overlapping.wait(0.25)
        release.set()
        for future in futures:
            future.result(timeout=3)
    r.close()
    assert not did_overlap, "native station requests overlapped despite the global lock"


def test_overlap_or_silence_does_not_demote_healthy_whistle(tmp_path):
    r, calls, workers = runtime(tmp_path)
    for index in range(12):
        audio = chunk(index=index).model_copy(
            update={"start_seconds": 100 + index * 5, "end_seconds": 110 + index * 5}
        )
        assert next(iter(r.transcribe([audio]))).text == "five to two"
    assert not r._recoveries
    assert all(call[0] == "whistle" for call in calls)
    assert not workers[0].closed
    assert not hasattr(r, "prepare_stabilization")
    r.close()


def test_pipeline_publishes_first_whistle_result_without_waiting(tmp_path):
    from civiccast.captions.pipeline import CaptionPipeline
    from civiccast.captions.stabilize import CaptionStabilizer

    r, _, _ = runtime(tmp_path)
    pipeline = CaptionPipeline(r, stabilizer=CaptionStabilizer(live=True))
    result = pipeline.process([chunk()], asset_id="public")
    assert [c.text for c in result.committed_cues] == ["five to two"]
    assert not pipeline.process([chunk()], asset_id="public").committed_cues
    assert not r._recoveries
    r.close()


def test_mixed_routing_keeps_two_channels_on_existing_whisper(tmp_path):
    from civiccast.captions.whistle import MixedCaptionRuntime

    primary, calls, _ = runtime(tmp_path)

    class ExistingWhisper:
        def __init__(self):
            self.calls = []
            self.prepared = False

        def prepare(self):
            self.prepared = True

        def transcribe(self, chunks, vocabulary=None):
            self.calls.extend(chunks)
            return []

        def on_cuda(self):
            return True

    whisper = ExistingWhisper()
    mixed = MixedCaptionRuntime(primary, whisper, {"public"})
    public = chunk()
    assert next(mixed.transcribe([public])).text == "five to two"
    assert list(mixed.transcribe([chunk("government"), chunk("education")])) == []
    assert [audio.chunk_id.split("-tap-")[0] for audio in whisper.calls] == [
        "government",
        "education",
    ]
    assert [call[0] for call in calls] == ["whistle"]
    assert mixed.channel_workers == 3
    assert mixed.on_cuda()
    mixed.prepare()
    assert whisper.prepared
    mixed.close()
    import pytest

    with pytest.raises(RuntimeError, match="closed"):
        list(mixed.transcribe([public]))


def test_mixed_cpu_whisper_keeps_single_call_safety(tmp_path):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from civiccast.captions.whistle import MixedCaptionRuntime

    primary, _, _ = runtime(tmp_path)
    guard = threading.Lock()
    start = threading.Barrier(2)
    active = peak = 0

    class CPUWhisper:
        def on_cuda(self):
            return False

        def transcribe(self, chunks, vocabulary=None):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.08)
            with guard:
                active -= 1
            return []

    mixed = MixedCaptionRuntime(primary, CPUWhisper(), {"public"})

    def run(channel):
        start.wait(timeout=2)
        return list(mixed.transcribe([chunk(channel)]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, channel) for channel in ("government", "education")]
        assert [future.result(timeout=3) for future in futures] == [[], []]
    assert peak == 1
    mixed.close()


def test_warm_channel_loads_once_before_first_audio(tmp_path, monkeypatch):
    from threading import Event

    r, calls, workers = runtime(tmp_path)
    monkeypatch.setattr(r, "prepare", lambda: None)
    r.warm_channel("public", Event())
    assert len(workers) == 1
    assert calls == []
    r.warm_channel("public", Event())
    list(r.transcribe([chunk()]))
    assert len(workers) == 1
    assert len(calls) == 1
    r.close()


def test_warm_channel_cancel_during_load_closes_unadopted_worker(tmp_path, monkeypatch):
    from threading import Event

    r, _, workers = runtime(tmp_path)
    monkeypatch.setattr(r, "prepare", lambda: None)
    factory = r._factory
    cancel = Event()

    def cancelled_factory(engine):
        result = factory(engine)
        cancel.set()
        return result

    r._factory = cancelled_factory
    r.warm_channel("public", cancel)
    assert workers[0].closed
    assert r._primaries == {}
    r.close()


def test_mixed_warmup_keeps_unselected_channels_on_whisper(tmp_path, monkeypatch):
    from threading import Event
    from unittest.mock import Mock

    from civiccast.captions.whistle import MixedCaptionRuntime

    r, _, workers = runtime(tmp_path)
    monkeypatch.setattr(r, "prepare", lambda: None)
    mixed = MixedCaptionRuntime(r, Mock(), {"public"})
    mixed.warm_channel("education", Event())
    assert workers == []
    mixed.warm_channel("public", Event())
    assert len(workers) == 1
    mixed.close()


def test_cold_warmup_does_not_hold_ready_channel_recognition(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    r, calls, _ = runtime(tmp_path)
    monkeypatch.setattr(r, "prepare", lambda: None)
    r.warm_channel("public", Event())
    entered, release = Event(), Event()
    factory = r._factory

    def held_factory(engine):
        entered.set()
        assert release.wait(5)
        return factory(engine)

    r._factory = held_factory
    with ThreadPoolExecutor(max_workers=2) as pool:
        warm = pool.submit(r.warm_channel, "education", Event())
        try:
            assert entered.wait(5)
            recognition = pool.submit(lambda: list(r.transcribe([chunk("public")])))
            assert recognition.result(timeout=2)[0].text == "five to two"
            assert len(calls) == 1
        finally:
            release.set()
            warm.result(timeout=5)
    r.close()


def test_warm_wait_does_not_create_a_primary_after_recovery_begins(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    r, _, workers = runtime(tmp_path)
    prepared = Event()
    monkeypatch.setattr(r, "prepare", prepared.set)
    r._primary_load_lock.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(r.warm_channel, "public", Event())
        try:
            assert prepared.wait(5)
            with r._guard:
                r._recoveries["public"] = object()
        finally:
            r._primary_load_lock.release()
        future.result(timeout=5)
    assert workers == []
    r._recoveries.clear()
    r.close()
