# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""F7 (RC4 field run 2026-11-04, this PC, education channel, reload 16).

A programme hand-over stopped half-way: the worker log shows
``ERROR: reload selector handoff failed: PermissionError(13, 'Access is denied')``
between ``stage=switching-selector`` and ``stage=holds-released``; the 15 s commit
watchdog then restarted the worker (~15 s of filler slide on air).

The failing call is the caption audio tap's ``program-boundary.json`` publish: a
temp file is ``replace``d over the target while the separate caption worker has
the target open to read it. On Windows a file opened without FILE_SHARE_DELETE
(every plain Python ``open``) refuses that replace with ERROR_ACCESS_DENIED. The
reader holds it for microseconds, so a short bounded retry clears it, and a
caption bookkeeping file must never take the video hand-over down.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from civiccast.egress.gst import audio_tap
from civiccast.egress.gst.audio_tap import (
    RollingWavSegmentWriter,
    read_program_boundary,
)


def _writer(tmp_path: Path) -> RollingWavSegmentWriter:
    return RollingWavSegmentWriter(tmp_path, segment_seconds=0.01, sample_rate_hz=1000)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file-sharing semantics")
def test_publish_succeeds_while_the_caption_worker_briefly_holds_the_file(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    try:
        writer.publish_program_boundary("old", 0)
        target = tmp_path / "program-boundary.json"
        reader = target.open("rb")  # what read_text() does in the caption worker
        released = threading.Timer(0.05, reader.close)
        released.start()
        try:
            writer.publish_program_boundary("new", 7)
        finally:
            released.join()
            reader.close()
        assert read_program_boundary(tmp_path) == ("new", 7)
    finally:
        writer.close()


def test_publish_gives_up_after_a_bounded_number_of_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = _writer(tmp_path)
    attempts: list[int] = []
    sleeps: list[float] = []

    def denied(self: Path, target: Path) -> Path:
        attempts.append(1)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", denied)
    monkeypatch.setattr(audio_tap.time, "sleep", sleeps.append)
    try:
        with pytest.raises(PermissionError):
            writer.publish_program_boundary("tok", 1)
    finally:
        monkeypatch.undo()
        writer.close()
    assert 1 < len(attempts) <= audio_tap.SHARING_RETRY_ATTEMPTS
    assert sum(sleeps) <= 0.25, "the retry runs on the engine main loop; keep it short"


def test_reader_retries_a_transient_sharing_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "program-boundary.json").write_text('{"token": "tok", "first_segment": 3}')
    real_read_text = Path.read_text
    denials = [PermissionError(13, "Access is denied")] * 2

    def flaky(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "program-boundary.json" and denials:
            raise denials.pop()
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr(audio_tap.time, "sleep", lambda _s: None)
    assert read_program_boundary(tmp_path) == ("tok", 3)
