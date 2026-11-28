# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""The stall watchdog also sees a cable (second mpegtsmux) that gets audio but no video.

The existing S9-5 watchdog (``_check_stall``) watches only ``self.mux`` -- the
first mpegtsmux, the web-preview HLS mux. On 2026-11-25 the CABLE mux
(``mux_cable_headend_*``) waited on a future-dated video frame for 1-1800 s:
cable aired AUDIO ONLY the whole time and nothing alarmed, because HLS kept
flowing and the watched mux was healthy. The cable worker then crashed.

These tests drive the REAL arm path (``_arm_stall_watchdog``) on a bare engine
against the same fake ``gi``/``Gst`` the reload-commit-ordering tests use, and
prove the alarm:

* A: cable audio advances, cable video does not, past ``stall_timeout_s`` ->
  the same quit path the existing watchdog uses, with a ``CTRL stall:`` line
  that names the cable mux.
* B: cable video advances normally -> no alarm.
* C: the first 60 s after start, or a reload commit in progress -> no alarm.
* D: no cable mux -> no cable probes, existing behaviour unchanged.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest

_ENGINE_MODULE_NAME = "civiccast.egress.gst.engine"
_CLOCK_TIME_NONE = (1 << 64) - 1
_CABLE_MUX = "mux_cable_headend_1"


class _FakePadProbeType:
    BUFFER = 1
    BUFFER_LIST = 2
    EVENT_DOWNSTREAM = 4
    IDLE = 8


class _FakePadProbeReturn:
    OK = "OK"
    DROP = "DROP"
    REMOVE = "REMOVE"


class _FakeIteratorResult:
    OK = "OK"
    DONE = "DONE"


class _FakePadIterator:
    def __init__(self, pads: list[Any]) -> None:
        self._pads = list(pads)
        self._index = 0

    def next(self) -> tuple[Any, Any]:
        if self._index >= len(self._pads):
            return _FakeIteratorResult.DONE, None
        pad = self._pads[self._index]
        self._index += 1
        return _FakeIteratorResult.OK, pad


class _FakeCaps:
    def __init__(self, text: str) -> None:
        self._text = text

    def to_string(self) -> str:
        return self._text


class _FakeMuxPad:
    """A mux SINK pad: takes probes, reports its negotiated caps, and fires them.

    Both muxes name their pads the same way in production (``sink_65`` video,
    ``sink_66`` audio), which is exactly why anything the watchdog keeps has to
    be keyed per mux AND pad. The caps are what resolve the stream label."""

    def __init__(self, name: str, caps: str) -> None:
        self.name = name
        self.caps = caps
        self.probes: list[tuple[Any, Any]] = []

    def get_name(self) -> str:
        return self.name

    def get_current_caps(self) -> _FakeCaps:
        return _FakeCaps(self.caps)

    def add_probe(self, mask: Any, callback: Any) -> str:
        self.probes.append((mask, callback))
        return f"{self.name}-probe-{len(self.probes)}"

    def remove_probe(self, _probe_id: Any) -> None:
        return None

    def set_property(self, _name: str, _value: Any) -> None:
        return None

    def connect(self, _signal: str, _callback: Any) -> int:
        return 0

    def fire_buffer(self) -> None:
        """Deliver one buffer the way the streaming thread would."""
        for mask, callback in list(self.probes):
            if mask == _FakePadProbeType.BUFFER:
                callback(self, None)


class _FakeMux:
    def __init__(self, pads: list[_FakeMuxPad]) -> None:
        self.pads = pads

    def iterate_sink_pads(self) -> _FakePadIterator:
        return _FakePadIterator(self.pads)


class _FakeLoop:
    def __init__(self) -> None:
        self.quit_calls = 0

    def quit(self) -> None:
        self.quit_calls += 1


class _Clock:
    """A controllable ``time`` module for the engine's monotonic reads."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:  # pragma: no cover - never used here
        self.now += seconds


def _install_fake_gst() -> types.ModuleType:
    fake_gst = types.ModuleType("gi.repository.Gst")
    fake_gst.IteratorResult = _FakeIteratorResult  # type: ignore[attr-defined]
    fake_gst.SECOND = 1_000_000_000  # type: ignore[attr-defined]
    fake_gst.MSECOND = 1_000_000  # type: ignore[attr-defined]
    fake_gst.CLOCK_TIME_NONE = _CLOCK_TIME_NONE  # type: ignore[attr-defined]
    fake_gst.PadProbeType = _FakePadProbeType  # type: ignore[attr-defined]
    fake_gst.PadProbeReturn = _FakePadProbeReturn  # type: ignore[attr-defined]
    return fake_gst


@pytest.fixture
def engine_module():
    """Load ``civiccast.egress.gst.engine`` fresh against a fake ``gi``/``Gst``
    -- same save/restore discipline as the reload-commit-ordering tests."""
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_repository = types.ModuleType("gi.repository")
    fake_glib = types.ModuleType("gi.repository.GLib")
    fake_glib.source_remove = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_glib.idle_add = lambda function, *args: function(*args)  # type: ignore[attr-defined]
    fake_glib.timeout_add_seconds = lambda *_a, **_k: 0  # type: ignore[attr-defined]
    fake_gst = _install_fake_gst()
    fake_repository.GLib = fake_glib  # type: ignore[attr-defined]
    fake_repository.Gst = fake_gst  # type: ignore[attr-defined]
    fake_gi.repository = fake_repository  # type: ignore[attr-defined]

    patched_names = ("gi", "gi.repository", "gi.repository.GLib", "gi.repository.Gst")
    saved = {name: sys.modules.get(name) for name in (*patched_names, _ENGINE_MODULE_NAME)}
    for name in patched_names:
        sys.modules[name] = {
            "gi": fake_gi,
            "gi.repository": fake_repository,
            "gi.repository.GLib": fake_glib,
            "gi.repository.Gst": fake_gst,
        }[name]
    sys.modules.pop(_ENGINE_MODULE_NAME, None)
    try:
        module = importlib.import_module(_ENGINE_MODULE_NAME)
        yield module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _hls_mux() -> _FakeMux:
    return _FakeMux([_FakeMuxPad("sink_65", "video/x-h264"), _FakeMuxPad("sink_66", "audio/mpeg")])


def _cable_mux() -> _FakeMux:
    return _FakeMux([_FakeMuxPad("sink_65", "video/x-h264"), _FakeMuxPad("sink_66", "audio/mpeg")])


def _bare_engine(module: types.ModuleType, clock: _Clock) -> Any:
    engine = object.__new__(module.GstPlayoutEngine)
    engine.stall_timeout_s = 10.0
    engine.first_output_timeout_s = 45.0
    engine.commit_timeout_s = 15.0
    engine._output_buffers = 0
    engine._output_bytes = 0
    engine._playing_reached_at = 0.0
    engine._first_output_marker_printed = False
    engine._pending_reload = None
    engine._error = None
    engine._loop = _FakeLoop()
    engine.audio_selector = None
    engine.mux = _hls_mux()
    # Production ``run()`` arms these counters before ``_arm_stall_watchdog``;
    # the bare engine follows the same order.
    engine._install_mux_input_counters()
    # An engine built for a channel WITHOUT a cable sink: production would not
    # set this attribute at all, so the bare engine does not either.
    return engine


def _advance_hls(engine: Any) -> None:
    """Keep the watched HLS mux healthy so only the cable check can fire."""
    engine._output_buffers += 100


def _fire(pad: _FakeMuxPad) -> None:
    pad.fire_buffer()


def _stall_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if "CTRL stall:" in line]


def _tick(engine: Any, clock: _Clock, monkeypatched: Any, *, seconds: float) -> bool:
    clock.now += seconds
    _advance_hls(engine)
    return engine._check_stall()


@pytest.fixture
def patched_clock(engine_module: Any, monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(engine_module, "time", clock)
    return clock


def test_a_cable_stall_alarm_fires_when_audio_advances_and_video_does_not(
    engine_module: Any,
    patched_clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _bare_engine(engine_module, patched_clock)
    cable = _cable_mux()
    # Production sets this in ``_build_output_branches``; the bare engine sets
    # the same attribute the build would have produced for a cable branch.
    engine._cable_muxes = [(_CABLE_MUX, cable)]

    engine._arm_stall_watchdog()
    cable_video, cable_audio = cable.pads

    keep_watching = True
    # 12 ticks of 5 s = 60 s: the grace period expires on the last one, and
    # cable audio has advanced on every tick while cable video has not once.
    for _ in range(13):
        _fire(cable_audio)
        keep_watching = _tick(engine, patched_clock, monkeypatch, seconds=5.0)
        if not keep_watching:
            break

    err = capsys.readouterr().err
    assert keep_watching is False, "the cable audio-only stall was not acted on"
    stalls = _stall_lines(err)
    assert stalls, f"no CTRL stall line; stderr was:\n{err}"
    assert _CABLE_MUX in stalls[-1], f"the line does not name the cable mux: {stalls[-1]}"
    # The same quit path the existing watchdog uses: a ("stall", ...) error and
    # a loop quit, which makes worker.py exit non-zero for the daemon restart.
    assert engine._error is not None and engine._error[0] == "stall"
    assert engine._loop.quit_calls == 1
    # Nothing was delivered on the cable VIDEO pad the whole time.
    assert cable_video.probes, "the cable video pad was never probed"


def test_b_a_cable_mux_whose_video_advances_never_alarms(
    engine_module: Any,
    patched_clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _bare_engine(engine_module, patched_clock)
    cable = _cable_mux()
    engine._cable_muxes = [(_CABLE_MUX, cable)]
    engine._arm_stall_watchdog()
    cable_video, cable_audio = cable.pads

    for _ in range(20):
        _fire(cable_video)
        _fire(cable_audio)
        assert _tick(engine, patched_clock, monkeypatch, seconds=5.0) is True

    assert _stall_lines(capsys.readouterr().err) == []
    assert engine._error is None
    assert engine._loop.quit_calls == 0


def test_c1_no_alarm_inside_the_first_60_seconds_after_arming(
    engine_module: Any,
    patched_clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _bare_engine(engine_module, patched_clock)
    cable = _cable_mux()
    engine._cable_muxes = [(_CABLE_MUX, cable)]
    engine._arm_stall_watchdog()
    _cable_video, cable_audio = cable.pads

    # 11 ticks of 5 s = 55 s: well past the 10 s bound, still inside the grace.
    for _ in range(11):
        _fire(cable_audio)
        assert _tick(engine, patched_clock, monkeypatch, seconds=5.0) is True

    assert _stall_lines(capsys.readouterr().err) == []
    assert engine._loop.quit_calls == 0

    # The grace ENDS at 60 s: the same stalled shape is then acted on.
    keeps_watching = True
    for _ in range(2):
        _fire(cable_audio)
        keeps_watching = _tick(engine, patched_clock, monkeypatch, seconds=5.0)
        if not keeps_watching:
            break

    assert keeps_watching is False, "the alarm did not fire once the grace expired"
    assert _stall_lines(capsys.readouterr().err)
    assert engine._loop.quit_calls == 1


def test_c2_no_alarm_while_a_reload_commit_is_in_progress(
    engine_module: Any,
    patched_clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _bare_engine(engine_module, patched_clock)
    cable = _cable_mux()
    engine._cable_muxes = [(_CABLE_MUX, cable)]
    engine._arm_stall_watchdog()
    engine._pending_reload = {"commit_in_progress": True}
    _cable_video, cable_audio = cable.pads

    for _ in range(14):
        _fire(cable_audio)
        assert _tick(engine, patched_clock, monkeypatch, seconds=5.0) is True

    assert _stall_lines(capsys.readouterr().err) == []
    assert engine._loop.quit_calls == 0

    # The commit settles: the cable reference restarts, so the video-flat clock
    # does not inherit the whole commit window -- but the suspension is not
    # permanent: the same stalled shape IS acted on a grace later.
    engine._pending_reload = None
    engine._reset_stall_reference()
    assert _tick(engine, patched_clock, monkeypatch, seconds=5.0) is True
    assert _stall_lines(capsys.readouterr().err) == []

    keeps_watching = True
    for _ in range(11):
        _fire(cable_audio)
        keeps_watching = _tick(engine, patched_clock, monkeypatch, seconds=5.0)
        if not keeps_watching:
            break

    assert keeps_watching is False, "the alarm never resumed after the commit settled"
    assert _stall_lines(capsys.readouterr().err)
    assert engine._loop.quit_calls == 1


def test_d_no_cable_mux_means_no_cable_probes_and_unchanged_behaviour(
    engine_module: Any,
    patched_clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _bare_engine(engine_module, patched_clock)
    hls = engine.mux

    engine._arm_stall_watchdog()
    assert _tick(engine, patched_clock, monkeypatch, seconds=15.0) is True

    assert getattr(engine, "_cable_mux_pads", {}) == {}
    assert getattr(engine, "_cable_mux_buffers", {}) == {}
    for pad in hls.pads:
        # Only the existing watchdog's own probes are installed on the HLS mux.
        assert all(callback.__name__ != "_count_cable_input" for _m, callback in pad.probes)
    assert _stall_lines(capsys.readouterr().err) == []
    assert engine._loop.quit_calls == 0
