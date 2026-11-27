# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""U44 on EVERY TS mux: the cable mux must drop the future-dated closing frame.

The U44 arrival guard was armed only from ``_install_mux_input_counters``, which
walks the sink pads of ``self.mux`` -- the FIRST mpegtsmux in the output
branches, i.e. the web-preview HLS mux. The cable branch has its own
``mpegtsmux`` (``mux_cable_headend_*``) on the same sink pad names (``sink_65``
video, ``sink_66`` audio) with NO guard, so at every program change that mux
waits for the ``videorate`` closing-duplicate frame -- one subchain in the
future, 1s to 1800s -- and then builds a buffer list large enough to blow
udpsink's stack (0xC00000FD in gstudp.dll). The worker crashes.

These tests go through the REAL install path: they set up an engine whose
``self.mux`` is the HLS fake and whose stored TS-mux list also holds the cable
fake, call ``_install_mux_input_counters()``, and then drive whichever guard
probe that call put on the cable pad. On the pre-fix engine no such probe
exists, so these fail on the missing guard -- never on a missing helper name.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest

from tests.egress.test_gst_engine_reload_commit_ordering import (
    _ENGINE_MODULE_NAME,
    _bare_engine_for_commit,
    _FakeClockPipeline,
    _FakeDiagnosticPad,
    _FakePadIterator,
    _FakeProbeBuffer,
    _FakeProbeInfo,
    _FakeSegment,
    _FakeStickyEvent,
    _install_fake_gst,
    _Recorder,
)


@pytest.fixture
def engine_module() -> Any:
    """Load ``civiccast.egress.gst.engine`` fresh against a fake ``gi``/``Gst``.

    The same save/restore discipline as the sibling module's fixture of the same
    name, which cannot simply be imported here (its name would clash with the
    parameter that requests it, and pytest resolves fixtures by name)."""
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_repository = types.ModuleType("gi.repository")
    fake_glib = types.ModuleType("gi.repository.GLib")
    fake_glib.source_remove = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_glib.idle_add = lambda function, *args: function(*args)  # type: ignore[attr-defined]
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


_SECOND = 1_000_000_000


class _FakeTsMux:
    """A minimal ``mpegtsmux``: sink pads, a name, and its signal connections.

    ``connect`` is what a later ``pad-added`` guard hangs off; production uses a
    request sink pad template, so pad-added is the only way a pad appears after
    install."""

    def __init__(self, name: str, pads: list[Any]) -> None:
        self._name = name
        self._pads = pads
        self.connections: dict[str, Any] = {}

    def get_name(self) -> str:
        return self._name

    def iterate_sink_pads(self) -> _FakePadIterator:
        return _FakePadIterator(self._pads)

    def connect(self, signal: str, callback: Any) -> int:
        self.connections[signal] = callback
        return 1


def _guard_probe(pad: _FakeDiagnosticPad) -> Any:
    """The arrival guard the install path put on ``pad`` -- or a clean failure.

    Asserting here (rather than falling back to calling the factory directly) is
    the whole point: the defect was a guard that existed but was never INSTALLED
    on the cable mux."""
    guards = [
        callback for _mask, callback in pad.probes if callback.__name__ == "_guard_mux_pad_arrival"
    ]
    assert guards, f"no arrival guard installed on pad {pad.get_name()}; probes={pad.probes}"
    return guards[-1]


def _video_pad_with_segment(
    name: str, recorder: _Recorder, pts_to_running_time: dict[int, int]
) -> _FakeDiagnosticPad:
    pad = _FakeDiagnosticPad(name, recorder, caps="video/x-h264, stream-format=byte-stream")
    pad.sticky = _FakeStickyEvent(
        _FakeSegment(base=0, running_time_for_pts=dict(pts_to_running_time))
    )
    return pad


def _push_arrival(guard: Any, pad: _FakeDiagnosticPad, running_time_ns: int) -> Any:
    """Fire the guard exactly as the pad's streaming thread would: PTS is mapped
    to running time through the pad's sticky segment."""
    segment = pad.sticky.parse_segment()
    segment._running_time_for_pts[running_time_ns] = running_time_ns
    return guard(pad, _FakeProbeInfo(_FakeProbeBuffer(running_time_ns)))


def _two_mux_engine(
    engine_module: types.ModuleType, recorder: _Recorder, *, pipeline_rt_ns: int
) -> tuple[Any, _FakeTsMux, _FakeTsMux, _FakeDiagnosticPad, _FakeDiagnosticPad]:
    """An engine with the production two-TS-mux shape, pre-install.

    ``self.mux`` is the HLS fake (the first TS mux, as ``_build_output_branches``
    chooses); the cable fake carries the SAME pad names in the stored TS-mux
    list. The pipeline clock is readable so the guard's "ahead of the clock" arm
    can actually be evaluated."""
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.pipeline = _FakeClockPipeline(recorder, clock_time_ns=pipeline_rt_ns, base_time_ns=0)
    hls_video = _video_pad_with_segment("sink_65", recorder, {})
    hls_audio = _FakeDiagnosticPad("sink_66", recorder, caps="audio/mpeg, mpegversion=4")
    cable_video = _video_pad_with_segment("sink_65", recorder, {})
    cable_audio = _FakeDiagnosticPad("sink_66", recorder, caps="audio/mpeg, mpegversion=4")
    hls_mux = _FakeTsMux("mux_web_preview__hls_0", [hls_video, hls_audio])
    cable_mux = _FakeTsMux("mux_cable_headend_0", [cable_video, cable_audio])
    engine.mux = hls_mux
    # Set on the OLD engine too, so the test runs against it and fails on the
    # missing cable guard rather than on an absent attribute.
    engine._ts_muxes = [hls_mux, cable_mux]
    return engine, hls_mux, cable_mux, hls_video, cable_video


def test_cable_mux_guard_drops_future_dated_closing_frame_on_the_cable_mux(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test A: through the real install path, the cable mux's video pad drops it.

    Pipeline running time 100s. A normal frame at 100s seeds the reference; the
    ``videorate`` closing duplicate arrives 80s later (step 80s > 2.5s cadence;
    80s ahead of the clock > 1s) and must be DROPPED, or this mux sleeps 80s."""
    recorder = _Recorder()
    engine, _hls_mux, _cable_mux, _hls_video, cable_video = _two_mux_engine(
        engine_module, recorder, pipeline_rt_ns=100 * _SECOND
    )

    engine._install_mux_input_counters()

    guard = _guard_probe(cable_video)
    ok = engine_module.Gst.PadProbeReturn.OK
    drop = engine_module.Gst.PadProbeReturn.DROP
    assert _push_arrival(guard, cable_video, 100 * _SECOND) == ok
    assert _push_arrival(guard, cable_video, 180 * _SECOND) == drop
    assert engine._mux_pad_arrival_drops == {"mux_cable_headend_0:sink_65": 1}
    assert "pad=mux_cable_headend_0:sink_65" in capsys.readouterr().err


def test_cable_mux_guard_state_is_keyed_per_mux_not_per_pad_name(
    engine_module: types.ModuleType,
) -> None:
    """Test B: both muxes use ``sink_65``; a cable drop must not move HLS's
    reference or its drop count, and vice versa.

    Keyed by pad name alone, one mux's future frame would re-seed the other's
    reference and could suppress its drop (or falsely drop its real frame)."""
    recorder = _Recorder()
    engine, _hls_mux, _cable_mux, hls_video, cable_video = _two_mux_engine(
        engine_module, recorder, pipeline_rt_ns=100 * _SECOND
    )

    engine._install_mux_input_counters()

    hls_guard = _guard_probe(hls_video)
    cable_guard = _guard_probe(cable_video)
    ok = engine_module.Gst.PadProbeReturn.OK
    drop = engine_module.Gst.PadProbeReturn.DROP

    # Seed both references at 100s.
    assert _push_arrival(hls_guard, hls_video, 100 * _SECOND) == ok
    assert _push_arrival(cable_guard, cable_video, 100 * _SECOND) == ok

    # A future frame ONLY on the cable mux.
    assert _push_arrival(cable_guard, cable_video, 180 * _SECOND) == drop
    assert engine._mux_pad_arrival_drops == {"mux_cable_headend_0:sink_65": 1}
    # HLS's own reference and tally are untouched.
    assert engine._mux_pad_arrival_rt["sink_65"] == 100 * _SECOND
    assert "mux_web_preview__hls_0:sink_65" not in engine._mux_pad_arrival_drops

    # And the HLS mux still judges ITS pads against 100s, not the cable 180s.
    assert _push_arrival(hls_guard, hls_video, 180 * _SECOND) == drop
    assert engine._mux_pad_arrival_drops == {
        "mux_cable_headend_0:sink_65": 1,
        "sink_65": 1,
    }


def test_cable_mux_guard_covers_a_pad_added_after_install(
    engine_module: types.ModuleType,
) -> None:
    """Test C: a cable sink pad that appears after install (pad-added) is
    guarded too.

    The mux requests its sink pads lazily; a guard installed only over the pads
    present at worker start would leave a later pad unguarded."""
    recorder = _Recorder()
    engine, _hls_mux, cable_mux, _hls_video, _cable_video = _two_mux_engine(
        engine_module, recorder, pipeline_rt_ns=100 * _SECOND
    )
    cable_mux._pads = []  # only the pad-added path can reach this pad
    cable_mux.connections.clear()

    engine._install_mux_input_counters()

    assert "pad-added" in cable_mux.connections, cable_mux.connections
    late_video = _video_pad_with_segment("sink_77", recorder, {})
    cable_mux.connections["pad-added"](cable_mux, late_video)

    guard = _guard_probe(late_video)
    ok = engine_module.Gst.PadProbeReturn.OK
    drop = engine_module.Gst.PadProbeReturn.DROP
    assert _push_arrival(guard, late_video, 100 * _SECOND) == ok
    assert _push_arrival(guard, late_video, 180 * _SECOND) == drop
    assert engine._mux_pad_arrival_drops == {"mux_cable_headend_0:sink_77": 1}


def test_cable_mux_guard_keeps_an_ordinary_next_frame(
    engine_module: types.ModuleType,
) -> None:
    """Test D: a normal 33ms step on the cable mux is NOT dropped.

    The guard must fire only on the closing duplicate (a whole subchain ahead),
    never on the ordinary video cadence."""
    recorder = _Recorder()
    engine, _hls_mux, _cable_mux, _hls_video, cable_video = _two_mux_engine(
        engine_module, recorder, pipeline_rt_ns=100 * _SECOND
    )

    engine._install_mux_input_counters()

    guard = _guard_probe(cable_video)
    ok = engine_module.Gst.PadProbeReturn.OK
    assert _push_arrival(guard, cable_video, 100 * _SECOND) == ok
    assert _push_arrival(guard, cable_video, 100 * _SECOND + 33_000_000) == ok
    assert engine._mux_pad_arrival_drops == {}
