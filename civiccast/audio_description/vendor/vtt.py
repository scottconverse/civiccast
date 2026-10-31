# SPDX-License-Identifier: MIT
# Copyright (c) Microsoft Corporation
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import webvtt  # type: ignore[import-untyped]
from webvtt.errors import MalformedCaptionError, MalformedFileError  # type: ignore[import-untyped]
from webvtt.models import Timestamp  # type: ignore[import-untyped]


class VTTError(ValueError):
    pass


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def format_timestamp(seconds: float) -> str:
    milliseconds = round(max(0.0, seconds) * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _seconds(timestamp: webvtt.models.Timestamp) -> float:
    hours, minutes, seconds, milliseconds = timestamp.to_tuple()
    return float(hours * 3600 + minutes * 60 + seconds + milliseconds / 1000)


def parse_timestamp(value: str) -> float:
    try:
        if not Timestamp.PATTERN.fullmatch(value):
            raise MalformedCaptionError(f"Invalid timestamp {value!r}")
        return _seconds(Timestamp.from_string(value))
    except MalformedCaptionError as exc:
        raise VTTError(f"Malformed timestamp: {value!r}.") from exc


def write(cues: Sequence[Cue], output_vtt: str | Path) -> Path:
    output = Path(output_vtt)
    output.parent.mkdir(parents=True, exist_ok=True)
    captions = [
        webvtt.Caption(
            format_timestamp(cue.start),
            format_timestamp(cue.end),
            cue.text,
            identifier=str(number),
        )
        for number, cue in enumerate(cues, start=1)
    ]
    document = webvtt.WebVTT(
        captions=captions,
        header_comments=["Audio description track"],
    )
    document.save(str(output))
    return output


def read(input_vtt: str | Path) -> tuple[Cue, ...]:
    path = Path(input_vtt)
    try:
        captions = webvtt.read(str(path), encoding="utf-8-sig")
    except MalformedCaptionError as exc:
        raise VTTError(f"Malformed timestamp or cue in {path}: {exc}") from exc
    except (OSError, MalformedFileError) as exc:
        raise VTTError(f"Malformed VTT file {path}: {exc}") from exc

    cues: list[Cue] = []
    for number, caption in enumerate(captions, start=1):
        start = _seconds(caption.start_time)
        end = _seconds(caption.end_time)
        text = caption.raw_text.strip()
        if end <= start:
            raise VTTError(f"VTT cue {number} must end after it starts.")
        if not text:
            raise VTTError(f"VTT cue {number} has no text.")
        if cues and start < cues[-1].end:
            raise VTTError(f"VTT cue {number} overlaps cue {number - 1}.")
        cues.append(Cue(start, end, text))
    return tuple(cues)
