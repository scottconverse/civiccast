# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Atomic live WebVTT publication tests."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import pytest

import civiccast.captions.live_sidecar as sidecar_module
from civiccast.captions.live_sidecar import (
    LiveWebVttPublisher,
    publish_caption_runtime_status,
    reset_existing_live_sidecars,
)
from civiccast.captions.models import CaptionCue
from civiccast.egress.caption_embed import load_caption_cues_from_timed_text


def _cue(text: str) -> CaptionCue:
    return CaptionCue(
        cue_id="cue-1",
        start_seconds=1.0,
        end_seconds=3.0,
        text=text,
        confidence=0.91,
    )


def test_publishes_complete_webvtt_atomically(tmp_path: Path) -> None:
    active = tmp_path / "gov" / "captions" / "active.vtt"
    publisher = LiveWebVttPublisher(active)

    publisher.publish([_cue("motion carries")])

    cues = load_caption_cues_from_timed_text(active, source_id="gov")
    assert [(cue.text, cue.start_seconds, cue.end_seconds) for cue in cues] == [
        ("motion carries", 1.0, 3.0)
    ]
    assert list(active.parent.glob("*.tmp")) == []


def test_failed_replace_preserves_the_prior_complete_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = tmp_path / "gov" / "captions" / "active.vtt"
    publisher = LiveWebVttPublisher(active)
    publisher.publish([_cue("old complete cue")])
    before = active.read_bytes()

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(sidecar_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        publisher.publish([_cue("partial replacement must not leak")])

    assert active.read_bytes() == before
    assert list(active.parent.glob("*.tmp")) == []


def test_worker_restart_resets_only_active_sidecars(tmp_path: Path) -> None:
    active = tmp_path / "gov" / "captions" / "active.vtt"
    publisher = LiveWebVttPublisher(active)
    publisher.publish([_cue("stale cue")])
    retained = active.parent / "evidence" / "retained.wav"
    retained.parent.mkdir()
    retained.write_bytes(b"retained")

    reset_existing_live_sidecars(tmp_path)

    assert load_caption_cues_from_timed_text(active, source_id="gov") == []
    assert retained.read_bytes() == b"retained"


def test_storage_refusal_clears_stale_active_vtt_and_records_the_refusal(tmp_path: Path) -> None:
    active = tmp_path / "gov" / "captions" / "active.vtt"
    LiveWebVttPublisher(active).publish([_cue("stale caption")])

    status = publish_caption_runtime_status(
        tmp_path,
        "gov",
        state="storage-refused",  # type: ignore[arg-type]
        backlog_segments=0,
        max_backlog_segments=2,
        refusal_reason="caption-storage-volumes-diverge",
    )

    assert load_caption_cues_from_timed_text(active, source_id="gov") == []
    payload = __import__("json").loads(status.read_text(encoding="utf-8"))
    assert payload["state"] == "storage-refused"
    assert payload["refusal_reason"] == "caption-storage-volumes-diverge"


def test_transient_replace_failure_is_retried_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient handle on the temp file must not lose the publish.

    Field defect (beta.9 three-channel ladder, 2026-09-19): the installed
    service logged ``PermissionError [WinError 5]`` from the atomic
    temp->active replace. A short bounded retry recovers the case where the
    refusing handle is released quickly (e.g. real-time scanner / indexer).
    """

    active = tmp_path / "gov" / "captions" / "active.vtt"
    publisher = LiveWebVttPublisher(active)
    calls = {"n": 0}
    real_replace = sidecar_module.os.replace

    def flaky_replace(source: Path, destination: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("[WinError 5] Access is denied (transient)")
        real_replace(source, destination)

    monkeypatch.setattr(sidecar_module.os, "replace", flaky_replace)
    publisher.publish([_cue("recovered after a transient handle")])

    assert calls["n"] == 2, "the replace must be retried after a transient failure"
    cues = load_caption_cues_from_timed_text(active, source_id="gov")
    assert [cue.text for cue in cues] == ["recovered after a transient handle"]
    assert list(active.parent.glob("*.tmp")) == []


def test_persistent_replace_failure_is_reraised_after_bounded_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanent failure must still surface -- never a silent success.

    The retry is bounded (a mitigation, not a fix for the trigger): after the
    attempts are exhausted the original error is re-raised so the caller can
    record it.
    """

    active = tmp_path / "gov" / "captions" / "active.vtt"
    publisher = LiveWebVttPublisher(active)
    publisher.publish([_cue("old complete cue")])
    before = active.read_bytes()
    calls = {"n": 0}

    def always_fail(_source: Path, _destination: Path) -> None:
        calls["n"] += 1
        raise PermissionError("[WinError 5] Access is denied (persistent)")

    monkeypatch.setattr(sidecar_module.os, "replace", always_fail)
    monkeypatch.setattr(sidecar_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        publisher.publish([_cue("must not be lost")])

    assert calls["n"] == sidecar_module._ATOMIC_REPLACE_ATTEMPTS, (
        "the retry must be bounded, not unlimited"
    )
    assert active.read_bytes() == before
    assert list(active.parent.glob("*.tmp")) == []


def test_replace_retry_outlasts_the_station_reader_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#342: the writer gave up after ~0.15 s while a reader held active.vtt.

    The cable embed / proof reader (``caption_embed._read_timed_text``) holds the
    file for up to its own retry cycle. The writer's waits must grow and must
    outlast that cycle, so a held handle is waited out instead of raised.
    """

    from civiccast.egress import caption_embed

    schedule = sidecar_module._ATOMIC_REPLACE_BACKOFF_SCHEDULE_SECONDS
    assert list(schedule) == sorted(schedule), "the retry wait must grow"
    reader_cycle = (
        caption_embed._ACTIVE_VTT_READ_ATTEMPTS - 1
    ) * caption_embed._ACTIVE_VTT_READ_BACKOFF_SECONDS
    assert sum(schedule) > 10 * reader_cycle

    active = tmp_path / "public" / "captions" / "active.vtt"
    publisher = LiveWebVttPublisher(active)
    slept: list[float] = []
    calls = {"n": 0}
    real_replace = sidecar_module.os.replace

    def held_for_a_while(source: Path, destination: Path) -> None:
        calls["n"] += 1
        if calls["n"] <= 5:
            raise PermissionError("[WinError 5] Access is denied (reader holds the file)")
        real_replace(source, destination)

    monkeypatch.setattr(sidecar_module.os, "replace", held_for_a_while)
    monkeypatch.setattr(sidecar_module.time, "sleep", slept.append)

    publisher.publish([_cue("Let's build it.")])

    assert slept == list(schedule[:5])
    cues = load_caption_cues_from_timed_text(active, source_id="public")
    assert [cue.text for cue in cues] == ["Let's build it."]


def test_refused_publish_carries_its_cues_into_the_next_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#342: a refused write may delay a caption, but must never drop one."""

    active = tmp_path / "public" / "captions" / "active.vtt"
    publisher = LiveWebVttPublisher(active)
    real_replace = sidecar_module.os.replace
    refuse = {"on": True}

    def maybe_refuse(source: Path, destination: Path) -> None:
        if refuse["on"]:
            raise PermissionError("[WinError 5] Access is denied")
        real_replace(source, destination)

    monkeypatch.setattr(sidecar_module.os, "replace", maybe_refuse)
    monkeypatch.setattr(sidecar_module.time, "sleep", lambda _seconds: None)

    first = CaptionCue(
        cue_id="cue-a", start_seconds=1.0, end_seconds=3.0, text="Let's build it.", confidence=0.9
    )
    second = CaptionCue(
        cue_id="cue-b", start_seconds=3.0, end_seconds=5.0, text="Next line.", confidence=0.9
    )
    with pytest.raises(PermissionError):
        publisher.publish([first])

    refuse["on"] = False
    publisher.publish([second])

    cues = load_caption_cues_from_timed_text(active, source_id="public")
    assert [cue.text for cue in cues] == ["Let's build it.", "Next line."]

    # Once written, the carried cue is not repeated by later publishes.
    publisher.publish([second])
    cues = load_caption_cues_from_timed_text(active, source_id="public")
    assert [cue.text for cue in cues] == ["Next line."]


def test_failed_replace_does_not_close_a_reused_reader_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim_path = tmp_path / "unrelated.txt"
    victim_path.write_text("reader survives", encoding="utf-8")
    allocated: list[int] = []
    readers = []
    real_mkstemp = sidecar_module.tempfile.mkstemp

    def tracked_mkstemp(*args, **kwargs):
        descriptor, name = real_mkstemp(*args, **kwargs)
        allocated.append(descriptor)
        return descriptor, name

    def fail_replace(_source: Path, _destination: Path) -> None:
        if not readers:
            readers.append(victim_path.open(encoding="utf-8"))
            assert readers[0].fileno() == allocated[0], "descriptor reuse is required"
        raise PermissionError("simulated sharing violation")

    monkeypatch.setattr(sidecar_module.tempfile, "mkstemp", tracked_mkstemp)
    monkeypatch.setattr(sidecar_module.os, "replace", fail_replace)
    monkeypatch.setattr(sidecar_module.time, "sleep", lambda _seconds: None)
    try:
        with pytest.raises(PermissionError, match="simulated sharing violation"):
            LiveWebVttPublisher(tmp_path / "active.vtt").publish([_cue("new")])
        read_error = None
        try:
            content = readers[0].read()
        except OSError as error:
            read_error = error
        assert read_error is None, "writer cleanup closed the unrelated reader"
        assert content == "reader survives"
        assert list(tmp_path.glob("*.tmp")) == []
    finally:
        for reader in readers:
            with suppress(OSError):
                reader.close()


def test_live_sidecar_bounds_history_by_time_and_count(tmp_path: Path) -> None:
    cues = [
        CaptionCue(
            cue_id=f"c{i}", start_seconds=i, end_seconds=i + 1, text=f"cue {i}", confidence=1
        )
        for i in range(10)
    ]
    active = tmp_path / "active.vtt"
    publisher = LiveWebVttPublisher(active, window_seconds=5, max_cues=3)
    publisher.publish(list(reversed(cues)))
    assert [cue.text for cue in load_caption_cues_from_timed_text(active, source_id="gov")] == [
        "cue 7",
        "cue 8",
        "cue 9",
    ]
    assert len(cues) == 10  # publication never mutates the archival caller's history


def test_live_sidecar_time_window_keeps_crossing_cue(tmp_path: Path) -> None:
    cues = [
        CaptionCue(
            cue_id=f"c{i}", start_seconds=i, end_seconds=i + 2, text=f"cue {i}", confidence=1
        )
        for i in (0, 5, 9)
    ]
    active = tmp_path / "active.vtt"
    LiveWebVttPublisher(active, window_seconds=5).publish(cues)
    assert [cue.text for cue in load_caption_cues_from_timed_text(active, source_id="gov")] == [
        "cue 5",
        "cue 9",
    ]


def test_default_publisher_preserves_complete_archival_history(tmp_path: Path) -> None:
    cues = [
        CaptionCue(
            cue_id=f"c{i}",
            start_seconds=i * 5,
            end_seconds=i * 5 + 1,
            text=f"cue {i}",
            confidence=1,
        )
        for i in range(600)
    ]
    active = tmp_path / "english.vtt"
    LiveWebVttPublisher(active).publish(cues)
    assert len(load_caption_cues_from_timed_text(active, source_id="archive")) == len(cues)


@pytest.mark.parametrize(
    "limits",
    [
        {"window_seconds": 0},
        {"window_seconds": -1},
        {"window_seconds": float("nan")},
        {"window_seconds": float("inf")},
        {"max_cues": 0},
        {"max_cues": -1},
    ],
)
def test_publisher_rejects_invalid_history_limits(tmp_path: Path, limits: dict) -> None:
    with pytest.raises(ValueError):
        LiveWebVttPublisher(tmp_path / "active.vtt", **limits)


def test_fdopen_failure_still_closes_owned_descriptor_and_removes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocated: list[int] = []
    real_mkstemp = sidecar_module.tempfile.mkstemp

    def tracked_mkstemp(*args, **kwargs):
        descriptor, name = real_mkstemp(*args, **kwargs)
        allocated.append(descriptor)
        return descriptor, name

    def fail_fdopen(*_args, **_kwargs):
        raise OSError("fdopen failed")

    monkeypatch.setattr(sidecar_module.tempfile, "mkstemp", tracked_mkstemp)
    monkeypatch.setattr(sidecar_module.os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="fdopen failed"):
        LiveWebVttPublisher(tmp_path / "active.vtt").publish([])
    with pytest.raises(OSError):
        sidecar_module.os.fstat(allocated[0])
    assert list(tmp_path.glob("*.tmp")) == []
