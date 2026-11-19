# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""rc.6.1: R7 power loss, 2026-11-15.

R7 lost power and came back with an EMPTY ``program-boundary.json``. Every
caption scan then raised on it: 14 control-plane errors after the reboot.

Two fixes, two sets of tests:

1. The publish writes a temp file, flushes and fsyncs it, and only THEN
   replaces the target atomically.
2. The reader treats an empty or broken file as "no boundary" (``("", 0)``)
   and logs ONE warning per file, not an error on every scan.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from civiccast.egress.gst import audio_tap
from civiccast.egress.gst.audio_tap import RollingWavSegmentWriter, read_program_boundary

_BOUNDARY = "program-boundary.json"


@pytest.fixture(autouse=True)
def _fresh_warning_state() -> None:
    audio_tap._broken_boundary_warned.clear()


def _writer(tmp_path: Path) -> RollingWavSegmentWriter:
    return RollingWavSegmentWriter(tmp_path, segment_seconds=0.01, sample_rate_hz=1000)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "caption program boundary" in r.getMessage()
    ]


# --- 1. the publish is power-loss safe ------------------------------------


def test_publish_fsyncs_the_full_temp_file_before_the_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_fsync = audio_tap.os.fsync
    real_replace = Path.replace

    def fsync(fd: int) -> None:
        temporary = tmp_path / "program-boundary.tmp"
        if temporary.exists():
            # At fsync time the temp file already holds the whole payload.
            events.append("fsync:" + temporary.read_text(encoding="utf-8"))
        else:
            events.append("fsync-dir")
        real_fsync(fd)

    def replace(self: Path, target: Path) -> Path:
        events.append(f"replace:{self.name}->{Path(target).name}")
        return real_replace(self, target)

    monkeypatch.setattr(audio_tap.os, "fsync", fsync)
    monkeypatch.setattr(Path, "replace", replace)
    writer = _writer(tmp_path)
    try:
        writer.publish_program_boundary("tok", 4)
    finally:
        monkeypatch.undo()
        writer.close()

    payload = json.dumps({"token": "tok", "first_segment": 4})
    assert events[0] == "fsync:" + payload
    assert events[1] == f"replace:program-boundary.tmp->{_BOUNDARY}"
    assert not (tmp_path / "program-boundary.tmp").exists()
    assert read_program_boundary(tmp_path) == ("tok", 4)


def test_a_failed_fsync_leaves_the_old_boundary_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = _writer(tmp_path)
    try:
        writer.publish_program_boundary("old", 1)

        def fsync_fails(_fd: int) -> None:
            raise OSError(5, "I/O error")

        monkeypatch.setattr(audio_tap.os, "fsync", fsync_fails)
        with pytest.raises(OSError):
            writer.publish_program_boundary("new", 9)
        monkeypatch.undo()
        # The target was never replaced by a file that did not reach the disk.
        assert read_program_boundary(tmp_path) == ("old", 1)
    finally:
        writer.close()


# --- 2. the reader tolerates an empty or broken file ------------------------


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   \n",
        "\x00\x00\x00\x00",
        '{"token": "tok", "first_seg',
        "[]",
        '{"token": "tok"}',
        '{"token": "", "first_segment": 0}',
        '{"token": 7, "first_segment": 0}',
        '{"token": "tok", "first_segment": "3"}',
    ],
    ids=[
        "empty",
        "whitespace",
        "nul-bytes",
        "truncated-json",
        "not-an-object",
        "missing-key",
        "empty-token",
        "token-not-text",
        "segment-not-int",
    ],
)
def test_an_empty_or_broken_file_reads_as_no_boundary(tmp_path: Path, content: str) -> None:
    (tmp_path / _BOUNDARY).write_text(content, encoding="utf-8")
    assert read_program_boundary(tmp_path) == ("", 0)


def test_a_file_that_is_not_utf8_reads_as_no_boundary(tmp_path: Path) -> None:
    (tmp_path / _BOUNDARY).write_bytes(b"\xff\xfe\xfa")
    assert read_program_boundary(tmp_path) == ("", 0)


def test_a_broken_file_warns_once_not_on_every_scan(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / _BOUNDARY).write_text("", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="civiccast.egress.gst.audio_tap"):
        for _scan in range(14):
            assert read_program_boundary(tmp_path) == ("", 0)
    assert len(_warnings(caplog)) == 1
    assert "empty file" in _warnings(caplog)[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_the_warning_rearms_after_a_good_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    boundary = tmp_path / _BOUNDARY
    with caplog.at_level(logging.WARNING, logger="civiccast.egress.gst.audio_tap"):
        boundary.write_text("", encoding="utf-8")
        read_program_boundary(tmp_path)
        boundary.write_text(json.dumps({"token": "good", "first_segment": 2}), encoding="utf-8")
        assert read_program_boundary(tmp_path) == ("good", 2)
        boundary.write_text("{", encoding="utf-8")
        read_program_boundary(tmp_path)
    assert len(_warnings(caplog)) == 2


def test_each_channel_gets_its_own_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for channel in ("public", "education", "government"):
        (tmp_path / channel).mkdir()
        (tmp_path / channel / _BOUNDARY).write_text("", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="civiccast.egress.gst.audio_tap"):
        for _scan in range(3):
            for channel in ("public", "education", "government"):
                read_program_boundary(tmp_path / channel)
    assert len(_warnings(caplog)) == 3


def test_a_missing_file_is_still_silent_legacy_no_fence(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="civiccast.egress.gst.audio_tap"):
        assert read_program_boundary(tmp_path) == ("", 0)
    assert _warnings(caplog) == []


def test_a_new_publish_replaces_an_empty_file(tmp_path: Path) -> None:
    (tmp_path / _BOUNDARY).write_text("", encoding="utf-8")
    writer = _writer(tmp_path)
    try:
        writer.publish_program_boundary("fresh", 0)
    finally:
        writer.close()
    assert read_program_boundary(tmp_path) == ("fresh", 0)
