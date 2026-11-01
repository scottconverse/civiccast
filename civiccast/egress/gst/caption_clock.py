# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Bounded delivery receipts on the emitted MPEG-TS clock; no audio retention."""

from __future__ import annotations

import json
import math
import os
import threading
from collections import deque
from pathlib import Path
from typing import TypedDict

# Pinned GStreamer 1.28.5 tsmux.c CLOCK_BASE, added to every written PES PTS/DTS.
# https://github.com/GStreamer/gstreamer/blob/1.28.5/subprojects/gst-plugins-bad/gst/mpegtsmux/tsmux/tsmux.c
GST_TS_CLOCK_BASE_SECONDS = 3600.0
TS_CLOCK_WRAP_SECONDS = (1 << 33) / 90000.0


class _DeliveryPage(TypedDict):
    text: str
    start_seconds: float
    end_seconds: float


class CaptionDeliveryClock:
    """Keep accepted loading intents and observed encoder presentation separate.

    delivered.json retains scheduled appsrc pages; presentation.json records
    completed EOC/EDM display intervals. Keep at most 512 pages/180 seconds.
    Neither receipt alone proves captions survived the mux or matched speech.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.pages: list[_DeliveryPage] = []
        self.presentation_path = path.with_name("presentation.json")
        self._lock = threading.RLock()
        self._publish_lock = threading.Lock()
        self._generation = 0
        self._program = ""
        self._pending: deque[str] = deque()
        self._active: tuple[str, float] | None = None
        self._presentation: list[_DeliveryPage] = []
        self._loading = False
        self._encoded_count = 0
        self._displayed_count = 0
        self._retired_count = 0
        self._errors: list[str] = []
        self._dirty = True
        self._publish()
        self.flush_presentation()

    def begin_program(self, token: str) -> None:
        """Fence retired caption branches; the incoming EDM closes the old display."""
        with self._lock:
            self._program = token
            self._retired_count += len(self._pending)
            self._pending.clear()
            self._loading = False
            self._changed()

    def observe_text(self, text: str, *, program: str) -> None:
        """Observe text entering the real encoder, without I/O on its streaming task."""
        with self._lock:
            if program != self._program:
                return
            if len(self._pending) >= 512:
                self._error("caption encoder queue exceeded receipt bound")
                return
            self._pending.append(text)
            self._encoded_count += 1
            self._changed()

    def observe_cc_data(self, data: bytes, pts_seconds: float, *, program: str) -> None:
        """Track actual pop-on EOC/EDM timestamps before the mux, never fitted offsets.

        appsrc PTS starts loading non-displayed CEA-608 memory. It is NOT when
        EOC displays that page. EDM or the next EOC determines its actual end.
        The emitted transport is still independently decoded and compared later.
        """
        with self._lock:
            if program != self._program:
                return
            if len(data) % 3 or not math.isfinite(pts_seconds) or pts_seconds < 0:
                self._error("invalid cc_data timestamp or triplet length")
                return
            for offset in range(0, len(data), 3):
                flags, first, second = data[offset : offset + 3]
                if not flags & 4 or flags & 3:  # valid CEA-608 field 1 only
                    continue
                first, second = first & 0x7F, second & 0x7F
                stamp = pts_seconds + GST_TS_CLOCK_BASE_SECONDS
                if (first, second) == (0x14, 0x2F):  # End Of Caption: swap display
                    if not self._loading:  # repeated EOC is not a second page
                        continue
                    self._loading = False
                    self._close_display(stamp)
                    if not self._pending:
                        self._error("display command without an encoded text page")
                        continue
                    self._active = self._pending.popleft(), stamp
                    self._displayed_count += 1
                    self._changed()
                elif (first, second) == (0x14, 0x2C):  # Erase Displayed Memory
                    self._close_display(stamp)
                elif (first, second) in ((0x14, 0x20), (0x14, 0x2E)):
                    self._loading = True

    def _error(self, message: str) -> None:
        if message not in self._errors:
            self._errors = [*self._errors, message][-16:]
        self._changed()

    def _changed(self) -> None:
        self._generation += 1
        self._dirty = True

    def invalidate(self, message: str) -> None:
        with self._lock:
            self._error(message)

    def _close_display(self, stamp: float) -> None:
        if self._active is None:
            return
        text, start = self._active
        self._active = None
        if stamp <= start:
            self._error("non-increasing caption display clock")
            return
        self._presentation.append({"text": text, "start_seconds": start, "end_seconds": stamp})
        self._presentation = [p for p in self._presentation if p["end_seconds"] > stamp - 180][
            -512:
        ]
        self._changed()

    def flush_presentation(self) -> bool:
        """Publish on the control loop, not the media thread. False removes idle callbacks."""
        if not self._publish_lock.acquire(blocking=False):
            return False
        try:
            with self._lock:
                if not self._dirty:
                    return False
                generation = self._generation
                document = {
                    "clock": "cea608-display-control-pts-plus-mpegts-base",
                    "pages": list(self._presentation),
                    "encoded_page_count": self._encoded_count,
                    "displayed_page_count": self._displayed_count,
                    "retired_page_count": self._retired_count,
                    "pending_page_count": len(self._pending),
                    "active_page_count": int(self._active is not None),
                    "errors": list(self._errors),
                }
            # Streaming observers never wait for filesystem I/O. Concurrent
            # observations remain dirty for the following control heartbeat.
            temporary = self.presentation_path.with_suffix(f".{os.getpid()}.tmp")
            try:
                temporary.write_text(json.dumps(document), encoding="utf-8")
                temporary.replace(self.presentation_path)
                with self._lock:
                    if generation == self._generation:
                        self._dirty = False
            finally:
                temporary.unlink(missing_ok=True)
        finally:
            self._publish_lock.release()
        return False

    def record(self, text: str, start_seconds: float, duration_seconds: float) -> None:
        self.pages.append(
            {
                "text": text,
                "start_seconds": start_seconds + GST_TS_CLOCK_BASE_SECONDS,
                "end_seconds": start_seconds + duration_seconds + GST_TS_CLOCK_BASE_SECONDS,
            }
        )
        cutoff = start_seconds + GST_TS_CLOCK_BASE_SECONDS - 180
        self.pages = [p for p in self.pages if float(p["end_seconds"]) > cutoff][-512:]
        self._publish()

    def _publish(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(self.pages), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def captured_video_pts_window(path: Path) -> tuple[float, float] | None:
    """Read video PES PTS from this capture, unwrapping the 33-bit TS clock.

    Only packet headers are inspected. A bounded capture must contain a real
    video PES timestamp; absent/corrupt clock metadata never produces a pass.
    """
    stamps: list[float] = []
    if path.stat().st_size > 64 * 1024 * 1024:
        return None
    with path.open("rb") as stream:
        while packet := stream.read(188):
            if len(packet) != 188 or packet[0] != 0x47:
                return None
            if not packet[1] & 0x40 or not packet[3] & 0x10:
                continue
            offset = 4
            if packet[3] & 0x20:
                offset += 1 + packet[4]
            pes = packet[offset:]
            if len(pes) < 14 or pes[:3] != b"\x00\x00\x01" or not 0xE0 <= pes[3] <= 0xEF:
                continue
            if not pes[7] & 0x80 or pes[8] < 5:
                continue
            p = pes[9:14]
            pts = (
                (((p[0] >> 1) & 7) << 30)
                | (p[1] << 22)
                | ((p[2] >> 1) << 15)
                | (p[3] << 7)
                | (p[4] >> 1)
            )
            seconds = pts / 90000.0
            if stamps:
                seconds += (
                    round((stamps[-1] - seconds) / TS_CLOCK_WRAP_SECONDS) * TS_CLOCK_WRAP_SECONDS
                )
            stamps.append(seconds)
    return (min(stamps), max(stamps)) if stamps else None
