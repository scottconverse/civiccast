# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""GI-free reload-commit ordering, stale-callback, logging, and watchdog tests.

The 93f9168 Sandbox candidate completed 94 reloads but wedged once while a
retirement thread synchronously pushed ``FLUSH_START`` from an outgoing tail
into ``input-selector``. Video EOS had started the commit while old audio was
still streaming, so selector handoff and flush handling raced on a live path.

The replacement protocol requests both selectors, releases the fully-prerolled
leg's first-buffer holds, waits for exact active-pad notifications/readback, then
DROP-fences and detaches the old tails before NULLing the isolated old leg. The
tests cover that order, GStreamer 1.28's two-phase switch, pre-handoff rollback,
stop races, transaction ownership, and diagnostics.

These tests load ``civiccast.egress.gst.engine`` fresh against a small fake
``gi``/``Gst`` (the same technique as the concat-naming tests). They exercise
the commit/dispose/EOS helpers without a real pipeline or main loop, covering
the lock-safe ordering, superseded/stale EOS containment, staged diagnostics,
and the independent commit-watchdog thread."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
import time
import types
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from scripts.ops.check_reload_preroll import check_log

_ENGINE_MODULE_NAME = "civiccast.egress.gst.engine"


class _Recorder:
    """One shared, ORDER-preserving call log every fake below appends to --
    the whole point of these tests is relative ordering, not call counts."""

    def __init__(self) -> None:
        self.calls: list[str] = []


class _FakeState:
    NULL = "NULL"
    PAUSED = "PAUSED"
    PLAYING = "PLAYING"
    VOID_PENDING = "VOID_PENDING"


class _FakeStateChangeReturn:
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ASYNC = "ASYNC"
    NO_PREROLL = "NO_PREROLL"


class _FakePadProbeType:
    BUFFER = 1
    BUFFER_LIST = 2
    EVENT_DOWNSTREAM = 4
    IDLE = 8


class _FakePadProbeReturn:
    OK = "OK"
    DROP = "DROP"
    # U16: the one-shot first-buffer observers remove themselves from the pad.
    # (The reload-readiness tests at (5) assign this attribute locally; having it
    # on the class as well is purely additive.)
    REMOVE = "REMOVE"


class _FakeMessageType:
    ERROR = "ERROR"
    EOS = "EOS"


class _FakeFormat:
    TIME = "TIME"


class _FakeEventType:
    SEGMENT = "SEGMENT"
    EOS = "EOS"


class _FakeIteratorResult:
    OK = "OK"
    DONE = "DONE"


class _FakeElementIterator:
    """Empty iterator: ``_element_count`` just needs SOMETHING that returns a
    non-OK result on the first ``.next()`` call so the count loop ends at 0 --
    the exact count is irrelevant to these ordering tests."""

    def next(self) -> tuple[str, None]:
        return "DONE", None


class _FakePipeline:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder
        self.state = _FakeState.PLAYING
        self.pending_state = _FakeState.VOID_PENDING

    def get_state(self, _timeout: int) -> tuple[str, str, str]:
        return _FakeStateChangeReturn.SUCCESS, self.state, self.pending_state

    def remove(self, element: _FakeOldElement) -> None:
        self.recorder.calls.append(f"pipeline.remove:{element.name}")

    def iterate_elements(self) -> _FakeElementIterator:
        return _FakeElementIterator()

    def get_clock(self) -> None:
        # The current engine's running-time reader asks the pipeline for its
        # clock even in tests that deliberately model an unreadable clock.
        return None

    def get_base_time(self) -> int:
        return 0


class _FakeHoldPad:
    """A new leg's tail pad, held by a blocking probe -- ``remove_probe`` is
    what ``_release_hold_probes`` calls to let its streaming thread go."""

    def __init__(self, name: str, recorder: _Recorder) -> None:
        self.name = name
        self.recorder = recorder

    def remove_probe(self, probe_id: Any) -> None:
        self.recorder.calls.append(f"remove_probe:{self.name}:{probe_id}")

    def add_probe(self, mask: Any, callback: Any) -> str:
        """Round-2 finding 1: the abort path installs a DROP probe here BEFORE it
        lifts the hold, so the aborted leg never pushes into the selector."""
        self.recorder.calls.append(f"add_probe:{self.name}:{mask}:{callback.__name__}")
        return f"{self.name}-drop-probe"

    def set_offset(self, offset: int) -> None:
        self.recorder.calls.append(f"set_offset:{self.name}:{offset}")


class _FakeSelector:
    """The input-selector (or audio-selector) ``_commit_reload``/
    ``_dispose_source_leg`` operate on."""

    def __init__(self, name: str, recorder: _Recorder) -> None:
        self.name = name
        self.recorder = recorder
        self._active_pad: Any = None
        self._handlers: dict[int, tuple[Any, tuple[Any, ...]]] = {}
        self._next_handler = 1

    def set_property(self, key: str, value: Any) -> None:
        value_name = getattr(value, "name", value)
        self.recorder.calls.append(f"{self.name}.set_property:{key}={value_name}")
        if key == "active-pad":
            self._active_pad = value
            for callback, args in list(self._handlers.values()):
                callback(self, None, *args)

    def get_property(self, key: str) -> Any:
        assert key == "active-pad"
        return self._active_pad

    def connect(self, signal: str, callback: Any, *args: Any) -> int:
        assert signal == "notify::active-pad"
        handler_id = self._next_handler
        self._next_handler += 1
        self._handlers[handler_id] = (callback, args)
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self._handlers.pop(handler_id, None)

    def release_request_pad(self, pad: _FakeOldPad) -> None:
        self.recorder.calls.append(f"{self.name}.release_request_pad:{pad.name}")


class _FakePeer:
    def __init__(self, name: str, recorder: _Recorder, *, fail_mask: Any = None) -> None:
        self.name = name
        self.recorder = recorder
        self.fail_mask = fail_mask

    def unlink(self, pad: _FakeOldPad) -> None:
        self.recorder.calls.append(f"peer.unlink:{self.name}->{pad.name}")

    def add_probe(self, mask: Any, callback: Any, *args: Any) -> str:
        self.recorder.calls.append(f"add_probe:{self.name}:{mask}:{callback.__name__}")
        if mask == self.fail_mask:
            raise RuntimeError(f"probe unavailable: {mask}")
        probe_id = f"{self.name}-probe-{mask}"
        if mask == _FakePadProbeType.IDLE:
            callback(self, None, *args)
        return probe_id

    def remove_probe(self, probe_id: Any) -> None:
        self.recorder.calls.append(f"remove_probe:{self.name}:{probe_id}")

    def push_event(self, event: Any) -> bool:
        self.recorder.calls.append(f"peer.push_event:{self.name}:{event}")
        return True


class _PendingSelector(_FakeSelector):
    """Model GStreamer 1.28: setter return precedes the actual active-pad switch."""

    def __init__(self, name: str, recorder: _Recorder) -> None:
        super().__init__(name, recorder)
        self._pending_pad: Any = None

    def set_property(self, key: str, value: Any) -> None:
        value_name = getattr(value, "name", value)
        self.recorder.calls.append(f"{self.name}.set_property:{key}={value_name}")
        assert key == "active-pad"
        self._pending_pad = value

    def apply_pending(self) -> None:
        self._active_pad = self._pending_pad
        self.recorder.calls.append(f"{self.name}.apply_pending")
        for callback, args in list(self._handlers.values()):
            callback(self, None, *args)


class _CoupledTailState:
    """The audio tail cannot report IDLE while video is IDLE-blocked."""

    def __init__(self) -> None:
        self.video_idle_blocked = False


class _CoupledOldPeer(_FakePeer):
    """Model the captions A/V scheduling dependency behind the Sandbox wedge."""

    def __init__(
        self, name: str, recorder: _Recorder, state: _CoupledTailState, *, video: bool
    ) -> None:
        super().__init__(name, recorder)
        self.state = state
        self.video = video

    def add_probe(self, mask: Any, callback: Any, *args: Any) -> str:
        self.recorder.calls.append(f"add_probe:{self.name}:{mask}:{callback.__name__}")
        probe_id = f"{self.name}-probe-{mask}"
        if mask == _FakePadProbeType.IDLE:
            if self.video:
                self.state.video_idle_blocked = True
                callback(self, None, *args)
            elif not self.state.video_idle_blocked:
                callback(self, None, *args)
        return probe_id


class _FakeOldPad:
    """The RETIRING leg's own selector-side request pad -- what
    ``_dispose_source_leg`` unlinks and releases. NOT expected to see any
    ``send_event`` call in the current design -- see
    ``test_dispose_source_leg_never_sends_flush_events``."""

    def __init__(self, name: str, recorder: _Recorder, peer: _FakePeer | None) -> None:
        self.name = name
        self.recorder = recorder
        self._peer = peer
        # U30: (probe_id, mask, callback, args) for every probe this pad took, so
        # a test can fire the callback the way the streaming thread would -- the
        # same contract ``_FakeDiagnosticPad.probes`` provides, one user_data
        # argument richer (the boundary probes carry the transaction id).
        self.probes: list[tuple[Any, Any, Any, tuple[Any, ...]]] = []

    def get_peer(self) -> _FakePeer | None:
        return self._peer

    def get_name(self) -> str:
        return self.name

    def get_parent_element(self) -> Any:
        return getattr(self, "parent_element", None)

    def add_probe(self, mask: Any, callback: Any, *_args: Any) -> str:
        self.recorder.calls.append(f"add_probe:{self.name}:{mask}:{callback.__name__}")
        probe_id = f"{self.name}-probe-{mask}"
        self.probes.append((probe_id, mask, callback, _args))
        return probe_id

    def remove_probe(self, probe_id: Any) -> None:
        self.recorder.calls.append(f"remove_probe:{self.name}:{probe_id}")

    @staticmethod
    def find_property(name: str) -> object | None:
        return object() if name == "always-ok" else None

    def set_property(self, name: str, value: Any) -> None:
        self.recorder.calls.append(f"{self.name}.set_property:{name}={value}")

    def send_event(self, event: Any) -> bool:  # pragma: no cover - must not be called
        self.recorder.calls.append(f"send_event:{self.name}:{event}")
        return True


class _FakeOldElement:
    """One of the retiring leg's elements -- ``set_state(NULL)``."""

    def __init__(self, name: str, recorder: _Recorder) -> None:
        self.name = name
        self.recorder = recorder

    def get_name(self) -> str:
        return self.name

    def get_factory(self) -> Any:
        return types.SimpleNamespace(get_name=lambda: "fake-element")

    def set_state(self, state: Any) -> str:
        self.recorder.calls.append(f"set_state:{self.name}:{state}")
        return _FakeStateChangeReturn.SUCCESS

    def get_state(self, timeout: Any) -> tuple[str, str, str]:
        """A bin that answered ASYNC settles to NULL within the bounded wait."""
        self.recorder.calls.append(f"get_state:{self.name}:{timeout}")
        return (_FakeStateChangeReturn.SUCCESS, _FakeState.NULL, _FakeState.NULL)


class _FakeEvent:
    @staticmethod
    def new_flush_start() -> str:
        return "FLUSH_START"


# --- U16 diagnostics: pads, segments and buffers a streaming thread would see ---

_CLOCK_TIME_NONE = (1 << 64) - 1


class _FakeSegment:
    """Minimal ``GstSegment`` whose ``to_running_time`` answers from a table.

    What these tests assert through it is the diagnostic's PLUMBING -- whatever
    this object answers is what gets printed -- never a claim about GStreamer's
    own ``set_offset`` segment arithmetic. That arithmetic is measured on a real
    packaged pipeline in section (7) at the end of this module.
    """

    def __init__(self, *, base: int, running_time_for_pts: dict[int, int]) -> None:
        self.base = base
        self._running_time_for_pts = running_time_for_pts

    def to_running_time(self, fmt: Any, pts: int) -> int:
        assert fmt == _FakeFormat.TIME
        return self._running_time_for_pts.get(pts, _CLOCK_TIME_NONE)


class _FakeStickyEvent:
    def __init__(self, segment: _FakeSegment) -> None:
        self._segment = segment

    def parse_segment(self) -> _FakeSegment:
        return self._segment


class _FakeProbeBuffer:
    def __init__(self, pts: int, duration: int = 0) -> None:
        self.pts = pts
        self.duration = duration


class _FakeProbeInfo:
    def __init__(self, buffer: Any) -> None:
        self._buffer = buffer

    def get_buffer(self) -> Any:
        return self._buffer


class _FakeClock:
    def __init__(self, clock_time_ns: int) -> None:
        self._clock_time_ns = clock_time_ns

    def get_time(self) -> int:
        return self._clock_time_ns


class _FakeClockPipeline(_FakePipeline):
    """A pipeline whose own running time the diagnostics can actually read."""

    def __init__(self, recorder: _Recorder, *, clock_time_ns: int, base_time_ns: int = 0) -> None:
        super().__init__(recorder)
        self._clock = _FakeClock(clock_time_ns)
        self._base_time_ns = base_time_ns

    def get_clock(self) -> _FakeClock:
        return self._clock

    def get_base_time(self) -> int:
        return self._base_time_ns


class _FakeDiagnosticPad:
    """A new-leg tail src pad / mux sink pad as the U16 diagnostics see it.

    ``add_probe`` takes the callback ALONE -- the real ``Gst.Pad.add_probe``
    signature minus its optional user_data, which is exactly how production arms
    these probes, so a test can fire the recorded callback the way a streaming
    thread would."""

    def __init__(
        self,
        name: str,
        recorder: _Recorder,
        *,
        sticky: Any = None,
        caps: str | None = None,
    ) -> None:
        self.name = name
        self.recorder = recorder
        self.sticky = sticky
        self.caps = caps
        self.probes: list[tuple[Any, Any]] = []

    def get_name(self) -> str:
        return self.name

    def add_probe(self, mask: Any, callback: Any) -> str:
        self.recorder.calls.append(f"add_probe:{self.name}:{mask}:{callback.__name__}")
        self.probes.append((mask, callback))
        return f"{self.name}-probe-{mask}"

    def remove_probe(self, probe_id: Any) -> None:
        self.recorder.calls.append(f"remove_probe:{self.name}:{probe_id}")

    def set_offset(self, offset: int) -> None:
        self.recorder.calls.append(f"set_offset:{self.name}:{offset}")

    def get_sticky_event(self, event_type: Any, index: int) -> Any:
        self.recorder.calls.append(f"get_sticky_event:{self.name}:{event_type}:{index}")
        return self.sticky

    def get_current_caps(self) -> Any:
        if self.caps is None:
            return None
        return types.SimpleNamespace(to_string=lambda: self.caps)


class _FakePadIterator:
    """``iterate_sink_pads()`` over a fixed pad list, then a DONE result."""

    def __init__(self, pads: list[Any]) -> None:
        self._pads = list(pads)
        self._index = 0

    def next(self) -> tuple[Any, Any]:
        if self._index >= len(self._pads):
            return _FakeIteratorResult.DONE, None
        pad = self._pads[self._index]
        self._index += 1
        return _FakeIteratorResult.OK, pad


class _FakeMux:
    def __init__(self, pads: list[Any]) -> None:
        self._pads = pads

    def iterate_sink_pads(self) -> _FakePadIterator:
        return _FakePadIterator(self._pads)


def _install_fake_gst() -> types.ModuleType:
    fake_gst = types.ModuleType("gi.repository.Gst")
    fake_gst.State = _FakeState  # type: ignore[attr-defined]
    fake_gst.StateChangeReturn = _FakeStateChangeReturn  # type: ignore[attr-defined]
    fake_gst.MessageType = _FakeMessageType  # type: ignore[attr-defined]
    fake_gst.IteratorResult = _FakeIteratorResult  # type: ignore[attr-defined]
    fake_gst.SECOND = 1_000_000_000  # type: ignore[attr-defined]
    # U16: the diagnostics read ``Gst.Format.TIME``, ``Gst.EventType``,
    # ``Gst.CLOCK_TIME_NONE`` and ``Gst.MSECOND``. This fake omitted them only
    # because no test had reached those paths before -- adding them is additive.
    # ``SECOND`` was 1 (a placeholder that made any ``x / Gst.SECOND`` print raw
    # nanoseconds); U16 is the first line whose ASSERTED value is a seconds
    # rendering, so it now carries the real nanosecond count. Offsets stay
    # nanosecond ints either way -- only the printed form changes.
    fake_gst.MSECOND = 1_000_000  # type: ignore[attr-defined]
    fake_gst.CLOCK_TIME_NONE = _CLOCK_TIME_NONE  # type: ignore[attr-defined]
    fake_gst.Format = _FakeFormat  # type: ignore[attr-defined]
    fake_gst.EventType = _FakeEventType  # type: ignore[attr-defined]
    fake_gst.PadProbeType = _FakePadProbeType  # type: ignore[attr-defined]
    fake_gst.PadProbeReturn = _FakePadProbeReturn  # type: ignore[attr-defined]
    fake_gst.Event = _FakeEvent  # type: ignore[attr-defined]
    return fake_gst


@pytest.fixture
def engine_module():
    """Load ``civiccast.egress.gst.engine`` fresh against a fake ``gi``/``Gst``
    -- no real GStreamer install required. Mirrors
    ``test_gst_engine_reload_concat_naming.py``'s fixture of the same name and
    the same sys.modules save/restore discipline."""
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


def _bare_engine_for_commit(module: types.ModuleType, recorder: _Recorder) -> Any:
    engine = object.__new__(module.GstPlayoutEngine)
    engine.selector = _FakeSelector("video_sel", recorder)
    engine.audio_selector = _FakeSelector("audio_sel", recorder)
    engine.selector_sink_pads = [None]
    engine.audio_sink_pads = [None]
    engine._source_leg_elements = [None]
    engine.pipeline = _FakePipeline(recorder)
    engine._pending_reload = None
    engine._abort_retire_threads = []
    engine._abort_retire_legs = []
    engine._reload_commit_thread = None
    engine._stopping = False
    engine._error = None
    engine._loop = None
    engine._pending_overlay_swaps = {}
    return engine


def _complete_commit_for_test(engine: Any, pending: dict[str, Any]) -> bool:
    """Run the three production phases serially while retaining their exact order."""
    pending.setdefault("commit_in_progress", True)
    pending.setdefault("commit_watchdog", None)
    pending.setdefault("commit_completed", None)
    pending.setdefault("retirement_result", None)
    engine._pending_reload = pending
    pending.setdefault("txn_id", 1)
    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)
    pending["retirement_result"] = engine._dispose_confirmed_old_leg(pending)
    return engine._finish_reload_commit(pending)


def _index_of(calls: list[str], prefix: str) -> int:
    for i, call in enumerate(calls):
        if call.startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} never called; calls={calls}")


@pytest.mark.parametrize(
    ("retirement_alive", "caption_attached", "unrelated_attached", "expected"),
    [
        (True, True, False, (2, 1)),
        (False, True, False, (2, 0)),
        (True, True, True, (3, 1)),
        (True, False, False, (1, 0)),
    ],
)
def test_commit_counts_only_attached_inflight_caption_children_as_retiring(
    engine_module, retirement_alive, caption_attached, unrelated_attached, expected
) -> None:
    """Completed caption leftovers and unrelated growth remain visible as leaks."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    caption, active, unrelated = object(), object(), object()
    thread = types.SimpleNamespace(is_alive=lambda: retirement_alive)
    engine._caption_retire_legs = [{"thread": thread, "elements": [caption]}]
    children = [active] + ([caption] if caption_attached else [])
    if unrelated_attached:
        children.append(unrelated)

    def iterator():
        # Retirement can finish after its identities were snapshotted; don't
        # recategorize that snapshot midway through the pipeline enumeration.
        thread.is_alive = lambda: False
        remaining = iter(children)
        return types.SimpleNamespace(
            next=lambda: next(
                ((engine_module.Gst.IteratorResult.OK, child) for child in remaining),
                (engine_module.Gst.IteratorResult.DONE, None),
            )
        )

    engine.pipeline.iterate_elements = iterator
    raw, retiring = engine._reload_element_counts()
    assert (raw, retiring) == expected
    assert (raw - retiring == 1) is (
        not unrelated_attached and (retirement_alive or not caption_attached)
    )
    assert engine._element_count() == len(children)


def test_commit_counts_restart_after_caption_removal_resync(engine_module, monkeypatch) -> None:
    """A child removed mid-enumeration cannot turn a partial count into a leak result."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    caption, active, unrelated = object(), object(), object()
    alive_calls: list[bool] = []

    def alive():
        alive_calls.append(True)
        return len(alive_calls) == 1

    engine._caption_retire_legs = [
        {"thread": types.SimpleNamespace(is_alive=alive), "elements": [caption]}
    ]
    monkeypatch.setattr(engine_module.Gst.IteratorResult, "RESYNC", "RESYNC", raising=False)

    class _RemovingIterator:
        def __init__(self):
            self.results = iter(
                [
                    (engine_module.Gst.IteratorResult.OK, caption),
                    (engine_module.Gst.IteratorResult.RESYNC, None),
                ]
            )
            self.resyncs = 0

        def next(self):
            return next(self.results, (engine_module.Gst.IteratorResult.DONE, None))

        def resync(self):
            self.resyncs += 1
            self.results = iter(
                [
                    (engine_module.Gst.IteratorResult.OK, active),
                    (engine_module.Gst.IteratorResult.OK, unrelated),
                ]
            )

    iterator = _RemovingIterator()
    engine.pipeline.iterate_elements = lambda: iterator
    assert engine._reload_element_counts() == (2, 0)
    assert iterator.resyncs == 1
    assert alive_calls == [True]  # retain the original ownership snapshot


def _unready_reload(recorder: _Recorder, txn_id: int = 1) -> dict[str, Any]:
    video = _FakeHoldPad("new-video", recorder)
    audio = _FakeHoldPad("new-audio", recorder)
    return {
        "txn_id": txn_id,
        "new_leg_ready": False,
        "holds_awaited": 2,
        "held_pads": set(),
        "ready_pads": set(),
        "readiness_probes": [],
        "new_src_pads": [video, audio],
        "hold_probes": [(video, 1), (audio, 2)],
        "new_video_pad": video,
        "new_audio_pad": audio,
        "new_elements": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, None),
        "old_audio_pad": None,
        "old_elements": [_FakeOldElement("old-program", recorder)],
        "switch_at_end_of_current": True,
        "old_leg_eos": True,
        "rebase_new_leg": True,
        "outgoing_end": {"video": {"end": 1, "segment": None}},
        "boundary_probes": [],
        "timeout_id": None,
        "defer_timeout_id": None,
        "on_settled": None,
    }


def test_f1_never_prerolled_reload_cannot_dispose_or_commit(engine_module, capsys) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.commit_timeout_s = 5.0
    pending = _unready_reload(recorder)
    engine._pending_reload = pending
    assert engine._commit_reload() is False
    thread = engine._reload_commit_thread
    if thread is not None:
        thread.join(timeout=1.0)
    assert recorder.calls == [], "an unready replacement must not switch or retire the old leg"
    assert engine._pending_reload is pending
    assert "committed (elements=" not in capsys.readouterr().out


@pytest.mark.parametrize("held", [False, True])
def test_f1_queued_readiness_cannot_ready_a_superseding_reload(
    engine_module, monkeypatch, held
) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    old = _unready_reload(recorder)
    current = _unready_reload(recorder, txn_id=2)
    if not held:
        for pending in (old, current):
            pending["hold_probes"] = []
            pending["holds_awaited"] = 0
            pending["readiness_probes"] = [
                (pad, i) for i, pad in enumerate(pending["new_src_pads"])
            ]
    engine._pending_reload = old
    queued: list[tuple[Any, tuple[Any, ...]]] = []
    monkeypatch.setattr(engine_module.GLib, "idle_add", lambda fn, *args: queued.append((fn, args)))
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")
    if held:
        engine._on_new_leg_hold(old["new_src_pads"][0], None, old["txn_id"])
    else:
        # The non-held path also queues a callback from a streaming thread.
        engine_module.Gst.PadProbeReturn.REMOVE = "REMOVE"
        engine._on_reload_first_buffer(old["new_video_pad"], None, old["txn_id"])
    engine._pending_reload = current
    for callback, args in queued:
        callback(*args)
    assert current["new_leg_ready"] is False
    assert current["holds_awaited"] == (2 if held else 0)
    assert commits == []


@pytest.mark.parametrize("deferred", [False, True])
def test_f1_unheld_video_cannot_commit_before_audio_prerolls(engine_module, deferred) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending = _unready_reload(recorder)
    pending["hold_probes"] = []
    pending["holds_awaited"] = 0
    pending["switch_at_end_of_current"] = deferred
    pending["readiness_probes"] = [(pad, i) for i, pad in enumerate(pending["new_src_pads"])]
    engine._pending_reload = pending
    engine_module.Gst.PadProbeReturn.REMOVE = "REMOVE"
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")
    video, audio = pending["new_src_pads"]
    engine._on_reload_first_buffer(video, None, pending["txn_id"])
    engine._on_reload_first_buffer(video, None, pending["txn_id"])
    assert pending["new_leg_ready"] is False
    assert commits == []
    engine._on_reload_first_buffer(audio, None, pending["txn_id"])
    assert pending["new_leg_ready"] is True
    assert commits == ["commit"]


@pytest.mark.parametrize("old_eos", [False, True])
def test_scheduled_start_waits_for_time_and_current_preroll(engine_module, monkeypatch, old_eos):
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending = _unready_reload(recorder)
    pending.update(switch_at_unix=105.0, old_leg_eos=old_eos)
    engine._pending_reload = pending
    clock, timers, commits = {"now": 100.0}, [], []
    monkeypatch.setattr(engine_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        engine_module.GLib,
        "timeout_add",
        lambda *args: timers.append(args) or len(timers),
        raising=False,
    )
    engine._commit_reload = lambda: commits.append("commit")
    video, audio = pending["new_src_pads"]
    engine._on_new_leg_hold(video, None, pending["txn_id"])
    assert not pending["new_leg_ready"] and not timers
    engine._on_new_leg_hold(audio, None, pending["txn_id"])
    assert pending["new_leg_ready"] and timers[0][0] >= 5000
    assert not commits
    engine._on_scheduled_start(pending["txn_id"] - 1)
    assert not commits
    clock["now"] = 105.0
    engine._on_scheduled_start(pending["txn_id"])
    assert commits == ["commit"]


def test_outgoing_eos_cannot_commit_scheduled_program_early(engine_module, monkeypatch):
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending = _unready_reload(recorder)
    pending.update(switch_at_unix=105.0, new_leg_ready=True, holds_awaited=0)
    engine._pending_reload = pending
    monkeypatch.setattr(engine_module.time, "time", lambda: 100.0)
    engine._begin_reload_commit = lambda _: pytest.fail("scheduled source selected early")
    assert engine._commit_reload() is False
    assert engine._force_deferred_boundary(pending["txn_id"], reason="stall") is False
    assert not pending.get("boundary_forced", False)


def test_a_lead_longer_than_the_defer_watchdog_cannot_cut_the_program_early(
    engine_module, monkeypatch
):
    """rc.8 MSI round 3 (item 2): the deferred-switch watchdog is a bound on
    holding two legs, never a boundary.

    The new leg can be ready a full rollover lead before the outgoing plan's
    end -- now ~1020 s for a cold 1800 s window, longer than
    ``defer_switch_timeout_s`` (900 s). Firing the watchdog at T-900 must not
    switch while the recorded boundary (``switch_at_unix``, sent by the daemon
    for every deferred rollover) is still ahead; at the boundary the
    scheduled-start release may force."""

    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending = _unready_reload(recorder)
    pending.update(
        switch_at_end_of_current=True,
        new_leg_ready=True,
        holds_awaited=0,
        old_leg_eos=False,
        switch_at_unix=3000.0,
    )
    engine._pending_reload = pending
    engine.defer_switch_timeout_s = 900.0
    clock = {"now": 2100.0}  # 900 s after readiness, 900 s before the boundary
    monkeypatch.setattr(engine_module.time, "time", lambda: clock["now"])
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")

    assert engine._on_defer_switch_timeout(pending["txn_id"]) is False
    assert commits == [], "the watchdog cut the programme 900 s before its boundary"
    assert not pending.get("boundary_forced", False)

    clock["now"] = 3000.0
    engine._on_scheduled_start(pending["txn_id"])
    assert commits == ["commit"], "at the recorded boundary the switch must happen"


def test_f1_duplicate_hold_cannot_substitute_for_unprerolled_audio(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending = _unready_reload(recorder)
    engine._pending_reload = pending
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")
    video, audio = pending["new_src_pads"]
    engine._on_new_leg_hold(video, None, pending["txn_id"])
    engine._on_new_leg_hold(video, None, pending["txn_id"])
    assert pending["new_leg_ready"] is False
    assert pending["holds_awaited"] == 1
    assert commits == []
    engine._on_new_leg_hold(audio, None, pending["txn_id"])
    assert pending["new_leg_ready"] is True
    assert commits == ["commit"]


@pytest.mark.parametrize("timer", ["_on_reload_timeout", "_on_defer_switch_timeout"])
def test_f1_stale_timer_cannot_abort_or_commit_new_transaction(engine_module, timer) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending = _unready_reload(recorder, txn_id=2)
    pending["old_leg_eos"] = False
    engine._pending_reload = pending
    actions: list[str] = []
    engine._commit_reload = lambda: actions.append("commit")
    engine._abort_pending_reload = actions.append
    assert getattr(engine, timer)(1) is False
    assert actions == []
    assert engine._pending_reload is pending
    assert pending["old_leg_eos"] is False


@pytest.mark.parametrize("missing", ["audio", "boundary"])
def test_f1_commit_requires_all_streams_and_due_boundary(engine_module, missing) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending = _unready_reload(recorder)
    pending["new_leg_ready"] = True
    if missing == "boundary":
        pending["holds_awaited"] = 0
        pending["old_leg_eos"] = False
    engine._pending_reload = pending
    engine._begin_reload_commit = lambda _pending: pytest.fail("premature selector switch")
    assert engine._commit_reload() is False
    assert engine._reload_commit_thread is None
    assert recorder.calls == []


def test_f1_commit_log_requires_current_preroll_holds(engine_module, capsys) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.commit_timeout_s = 5.0
    pending = _unready_reload(recorder)
    engine._pending_reload = pending
    video, audio = pending["new_src_pads"]
    engine._on_new_leg_hold(video, None, pending["txn_id"])
    assert engine._reload_commit_thread is None
    engine._on_new_leg_hold(audio, None, pending["txn_id"])
    thread = engine._reload_commit_thread
    if thread is not None:
        thread.join(timeout=1.0)
    lines = capsys.readouterr().out.splitlines()
    hold = next(i for i, line in enumerate(lines) if "(0 stream(s) still to preroll)" in line)
    proof = next(i for i, line in enumerate(lines) if "preroll verified (reload_id=1)" in line)
    fire = next(i for i, line in enumerate(lines) if "firing (reload_id=1)" in line)
    disposed = next(i for i, line in enumerate(lines) if "old leg disposed" in line)
    commit = next(i for i, line in enumerate(lines) if "committed (elements=" in line)
    assert hold < proof < fire < disposed < commit
    assert check_log("\n".join(lines)) == (1, [])
    assert engine._pending_reload is None


# --- (1) settle only the current reload's first A/V boundary --------------------


def test_deferred_commit_waits_for_both_outgoing_eos_and_tail_fill(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first EOS starts a bridge; settlement waits for the common A/V end."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video_pad = _FakeOldPad("old-video", recorder, peer=None)
    audio_pad = _FakeOldPad("old-audio", recorder, peer=None)
    commits: list[str] = []

    def _commit() -> None:
        commits.append("commit")
        engine._pending_reload = None

    engine._commit_reload = _commit  # type: ignore[method-assign]
    engine._pending_reload = {
        "boundary_probes": [(video_pad, "video-probe"), (audio_pad, "audio-probe")],
        "outgoing_eos_pads": set(),
        "txn_id": 1,
        "old_video_pad": video_pad,
        "old_audio_pad": audio_pad,
        "old_leg_eos": False,
        "new_leg_ready": True,
        "outgoing_end": {},
        "tail_padding_done": True,
        "tail_padding_started": False,
        "tail_padding_pad": None,
        "tail_padding_target_ns": None,
        "tail_padding_target_final": False,
        "tail_padding_wake": threading.Event(),
        "tail_padding_stop": threading.Event(),
        "tail_padding_thread": None,
        "tail_padding_start_id": None,
    }
    # Keep this a state-ordering unit test: the native emitted-output check owns
    # the real producer; this fake records its later completion explicitly.
    engine._schedule_tail_padding = lambda _pending: None  # type: ignore[method-assign]

    assert engine._on_old_leg_eos(video_pad, 1) is False
    assert commits == []
    assert engine._pending_reload["tail_padding_pad"] is video_pad
    assert engine._on_old_leg_eos(audio_pad, 1) is False
    assert commits == []
    assert engine._pending_reload["tail_padding_target_final"] is True
    engine._tail_padding_finished(1, None)
    assert commits == ["commit"]
    stderr = capsys.readouterr().err
    assert "CTRL reload: outgoing EOS observed stream=video (1/2 stream(s))" in stderr
    assert "CTRL reload: outgoing EOS observed stream=audio (2/2 stream(s))" in stderr
    assert "tail-padding-complete" in stderr


def test_switch_running_time_uses_common_maximum_without_trimming(engine_module) -> None:
    switch, trim, spread, exceeded = engine_module.GstPlayoutEngine._select_switch_running_time(
        [("video", 4_000_000_000), ("audio", 4_600_000_000)],
        max_trim_ns=2_000_000_000,
    )

    assert switch == 4_600_000_000
    assert trim is None
    assert spread == 600_000_000
    assert exceeded is False


def test_deferred_video_only_commit_still_settles_on_its_only_eos(engine_module) -> None:
    """The first-boundary trigger also supports legitimate video-only legs."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video_pad = _FakeOldPad("old-video", recorder, peer=None)
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")  # type: ignore[method-assign]
    engine._pending_reload = {
        "boundary_probes": [(video_pad, "video-probe")],
        "outgoing_eos_pads": set(),
        "txn_id": 1,
        "old_video_pad": video_pad,
        "old_audio_pad": None,
        "old_leg_eos": False,
        "new_leg_ready": True,
    }

    assert engine._on_old_leg_eos(video_pad, 1) is False
    assert commits == ["commit"]
    assert engine._pending_reload["old_leg_eos"] is True


def test_stale_outgoing_eos_cannot_settle_a_superseding_reload(engine_module) -> None:
    """An idle callback queued by an older leg is ignored after supersession."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    stale_pad = _FakeOldPad("superseded-video", recorder, peer=None)
    current_pad = _FakeOldPad("current-video", recorder, peer=None)
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")  # type: ignore[method-assign]
    engine._pending_reload = {
        "boundary_probes": [(current_pad, "current-probe")],
        "outgoing_eos_pads": set(),
        "txn_id": 1,
        "old_leg_eos": False,
        "new_leg_ready": True,
    }

    assert engine._on_old_leg_eos(stale_pad, 1) is False
    assert commits == []
    assert engine._pending_reload["outgoing_eos_pads"] == set()
    assert engine._pending_reload["old_leg_eos"] is False


def test_stale_outgoing_eos_cannot_settle_an_immediate_reload(engine_module) -> None:
    """An immediate reload has no boundary pads and rejects an older EOS callback."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    stale_pad = _FakeOldPad("superseded-video", recorder, peer=None)
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")  # type: ignore[method-assign]
    engine._pending_reload = {
        "boundary_probes": [],
        "outgoing_eos_pads": set(),
        "txn_id": 1,
        "old_leg_eos": True,
        "new_leg_ready": True,
    }

    assert engine._on_old_leg_eos(stale_pad, 1) is False
    assert commits == []
    assert engine._pending_reload["outgoing_eos_pads"] == set()


def test_stale_outgoing_eos_on_the_same_pad_cannot_settle_a_superseding_reload(
    engine_module,
) -> None:
    """Round-2 finding 5: pad identity alone was not enough to reject a stale EOS.

    The boundary probes live on the OUTGOING selector sink pads, and a superseded
    transaction and the one that replaced it watch the SAME pad object. The old
    ``pad in expected`` guard therefore admitted a stale ``idle_add`` EOS queued by
    the superseded transaction and let it settle -- and commit -- its successor.
    The transaction id the probe captured at install time is what distinguishes
    them."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    shared_pad = _FakeOldPad("outgoing-video", recorder, peer=None)
    commits: list[str] = []
    engine._commit_reload = lambda: commits.append("commit")  # type: ignore[method-assign]
    engine._pending_reload = {
        "boundary_probes": [(shared_pad, "current-probe")],
        "outgoing_eos_pads": set(),
        "txn_id": 7,
        "old_video_pad": shared_pad,
        "old_audio_pad": None,
        "old_leg_eos": False,
        "new_leg_ready": True,
    }

    # Queued by transaction 6, delivered after 7 replaced it, on the same pad.
    assert engine._on_old_leg_eos(shared_pad, 6) is False
    assert commits == []
    assert engine._pending_reload["outgoing_eos_pads"] == set()
    assert engine._pending_reload["old_leg_eos"] is False

    # The current transaction's own EOS on that very same pad still settles it.
    assert engine._on_old_leg_eos(shared_pad, 7) is False
    assert commits == ["commit"]


# --- (2) select, retire old leg with replacement held, then release --------------


def test_commit_releases_replacement_before_retiring_old_leg(engine_module) -> None:
    """Both holds lift after setters return, before retirement can begin."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    new_video_pad = object()
    new_audio_pad = object()
    hold_video = _FakeHoldPad("hold-video", recorder)
    hold_audio = _FakeHoldPad("hold-audio", recorder)
    old_video_pad = _FakeOldPad("old-video", recorder, peer=_FakePeer("old-video-peer", recorder))
    old_audio_pad = _FakeOldPad("old-audio", recorder, peer=_FakePeer("old-audio-peer", recorder))
    old_element = _FakeOldElement("old-elem", recorder)

    pending: dict[str, Any] = {
        "timeout_id": None,
        "defer_timeout_id": None,
        "new_video_pad": new_video_pad,
        "new_audio_pad": new_audio_pad,
        "rebase_new_leg": False,
        "hold_probes": [(hold_video, "probe-1"), (hold_audio, "probe-2")],
        "boundary_probes": [],
        "old_video_pad": old_video_pad,
        "old_audio_pad": old_audio_pad,
        "old_elements": [old_element],
        "new_elements": [],
        "on_settled": None,
    }

    result = _complete_commit_for_test(engine, pending)

    assert result is False  # one-shot GLib-source contract
    calls = recorder.calls

    switch_video = _index_of(calls, "video_sel.set_property:active-pad")
    switch_audio = _index_of(calls, "audio_sel.set_property:active-pad")
    old_null = _index_of(calls, "set_state:old-elem:NULL")
    release_video = _index_of(calls, "video_sel.release_request_pad:old-video")
    release_audio = _index_of(calls, "audio_sel.release_request_pad:old-audio")
    remove_video = _index_of(calls, "remove_probe:hold-video")
    remove_audio = _index_of(calls, "remove_probe:hold-audio")

    assert switch_video < remove_video < release_video < old_null, calls
    assert switch_audio < remove_audio < release_audio < old_null, calls


def test_commit_confirms_handoff_then_fences_and_releases_old_request_pads(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production path follows GStreamer's dynamic-unlink protocol.

    Both selector setters return before both replacement holds are released.
    Confirmed active-pad readback gates nonblocking DROP fences and request-pad
    release. Releasing each selector-owned request pad performs the detach; no
    manual peer unlink, IDLE probe, or synchronous FLUSH probe can deadlock the
    coupled A/V producer.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    engine.commit_timeout_s = 5.0
    monkeypatch.setattr(
        engine,
        "_arm_commit_watchdog",
        lambda: (types.SimpleNamespace(cancel=lambda: None), threading.Event()),
    )

    coupled = _CoupledTailState()
    old_video_peer = _CoupledOldPeer("old-video-peer", recorder, coupled, video=True)
    old_audio_peer = _CoupledOldPeer("old-audio-peer", recorder, coupled, video=False)
    hold_video = _FakeHoldPad("hold-video", recorder)
    hold_audio = _FakeHoldPad("hold-audio", recorder)
    pending: dict[str, Any] = {
        "txn_id": 22,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "switch_at_end_of_current": True,
        "old_leg_eos": True,
        "timeout_id": None,
        "defer_timeout_id": None,
        "probe_id": None,
        "new_video_pad": object(),
        "new_audio_pad": object(),
        "new_elements": [],
        "new_src_pads": [hold_video, hold_audio],
        "hold_probes": [(hold_video, "new-video-hold"), (hold_audio, "new-audio-hold")],
        "boundary_probes": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, old_video_peer),
        "old_audio_pad": _FakeOldPad("old-audio", recorder, old_audio_peer),
        "old_elements": [_FakeOldElement("old-elem", recorder)],
        "rebase_new_leg": False,
        "on_settled": None,
        "commit_in_progress": False,
        "retirement_result": None,
        "commit_watchdog": None,
        "commit_completed": None,
    }
    engine._pending_reload = pending

    assert engine._commit_reload() is False
    thread = engine._reload_commit_thread
    if thread is not None:
        thread.join(timeout=5.0)

    calls = recorder.calls
    video_drop = _index_of(calls, "add_probe:old-video-peer:7")
    audio_drop = _index_of(calls, "add_probe:old-audio-peer:7")
    switch_video = _index_of(calls, "video_sel.set_property:active-pad")
    switch_audio = _index_of(calls, "audio_sel.set_property:active-pad")
    release_video = _index_of(calls, "video_sel.release_request_pad:old-video")
    release_audio = _index_of(calls, "audio_sel.release_request_pad:old-audio")
    remove_drop_video = _index_of(calls, "remove_probe:old-video-peer")
    remove_drop_audio = _index_of(calls, "remove_probe:old-audio-peer")
    old_null = _index_of(calls, "set_state:old-elem:NULL")
    release_new_video = _index_of(calls, "remove_probe:hold-video")
    release_new_audio = _index_of(calls, "remove_probe:hold-audio")

    assert max(switch_video, switch_audio) < min(release_new_video, release_new_audio), calls
    assert max(release_new_video, release_new_audio) < min(video_drop, audio_drop), calls
    assert max(video_drop, audio_drop) < min(release_video, release_audio), calls
    assert max(release_video, release_audio) < old_null, calls
    assert old_null < min(remove_drop_video, remove_drop_audio), calls
    assert not any(":8:" in call for call in calls), calls
    assert not any(call.startswith("peer.unlink:") for call in calls), calls
    assert not any(call.startswith("peer.push_event:") for call in calls), calls


def test_confirmed_retirement_releases_a_peerless_selector_request_pad(engine_module) -> None:
    """A missing peer does not transfer ownership of the selector request pad."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    old_video_pad = _FakeOldPad("old-video", recorder, peer=None)
    pending: dict[str, Any] = {
        "old_video_pad": old_video_pad,
        "old_audio_pad": None,
        "old_elements": [],
        "old_tail_drop_probes": [],
    }

    ok, reason = engine._dispose_confirmed_old_leg(pending)

    assert ok is True
    assert reason is None
    assert recorder.calls.count("video_sel.release_request_pad:old-video") == 1
    assert not any(call.startswith("peer.unlink:") for call in recorder.calls)


def test_finite_commit_arms_selector_cutoffs_before_rebase_snapshot(engine_module) -> None:
    """A late old buffer cannot outrun the fresh replacement's sampled offset."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    old_video_pad = _FakeOldPad("old-video", recorder, peer=None)
    old_audio_pad = _FakeOldPad("old-audio", recorder, peer=None)
    new_video_src = _FakeHoldPad("new-video-src", recorder)
    new_audio_src = _FakeHoldPad("new-audio-src", recorder)

    class _ObservedEnds(dict[Any, dict[str, Any]]):
        def get(self, key: Any, default: Any = None) -> Any:  # type: ignore[override]
            recorder.calls.append("outgoing-end-snapshot")
            return super().get(key, default)

    pending: dict[str, Any] = {
        "txn_id": 28,
        "timeout_id": None,
        "defer_timeout_id": None,
        "new_video_pad": object(),
        "new_audio_pad": object(),
        "new_elements": [],
        "new_src_pads": [new_video_src, new_audio_src],
        "hold_probes": [(new_video_src, "video-hold"), (new_audio_src, "audio-hold")],
        "old_video_pad": old_video_pad,
        "old_audio_pad": old_audio_pad,
        "old_elements": [],
        "outgoing_end": _ObservedEnds(
            {
                old_video_pad: {"end": 1_000, "segment": None},
                old_audio_pad: {"end": 1_100, "segment": None},
            }
        ),
        "rebase_new_leg": True,
        "switch_at_end_of_current": False,
        "old_tail_drop_probes": [],
        "commit_in_progress": True,
    }
    engine._pending_reload = pending
    engine._prepare_reload_handoff(pending)

    engine._begin_reload_commit(pending)

    calls = recorder.calls
    video_cutoff = _index_of(calls, "add_probe:old-video:7:_drop_past_switch_point_probe")
    audio_cutoff = _index_of(calls, "add_probe:old-audio:7:_drop_past_switch_point_probe")
    snapshot = _index_of(calls, "outgoing-end-snapshot")
    video_offset = _index_of(calls, "set_offset:new-video-src:1100")
    audio_offset = _index_of(calls, "set_offset:new-audio-src:1100")
    switch_video = _index_of(calls, "video_sel.set_property:active-pad")
    switch_audio = _index_of(calls, "audio_sel.set_property:active-pad")
    release_video = _index_of(calls, "remove_probe:new-video-src:video-hold")
    release_audio = _index_of(calls, "remove_probe:new-audio-src:audio-hold")
    assert max(video_cutoff, audio_cutoff) < snapshot, calls
    assert snapshot < min(video_offset, audio_offset), calls
    assert max(video_offset, audio_offset) < min(switch_video, switch_audio), calls
    assert max(switch_video, switch_audio) < min(release_video, release_audio), calls
    assert pending["selector_handoff_confirmed"] is True


def test_captions_av_pending_selector_switches_confirm_before_old_tail_retirement(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reproduce the beta.7 captions-ON two-phase selector handoff.

    Both active-pad setters only queue pending switches. Removing both new-leg
    first-buffer holds applies them, and retirement may begin only after exact
    video and audio readback confirms the change. No old-tail IDLE probe is ever
    installed. The transaction settles once, reclaims the old element, and never
    invokes the commit-timeout exit.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    engine.commit_timeout_s = 5.0
    completed = threading.Event()
    monkeypatch.setattr(
        engine,
        "_arm_commit_watchdog",
        lambda: (types.SimpleNamespace(cancel=lambda: None), completed),
    )
    exit_calls: list[int] = []
    monkeypatch.setattr(engine_module.os, "_exit", exit_calls.append)

    video_selector = _PendingSelector("video_sel", recorder)
    audio_selector = _PendingSelector("audio_sel", recorder)
    engine.selector = video_selector
    engine.audio_selector = audio_selector
    coupled = _CoupledTailState()
    old_video_peer = _CoupledOldPeer("old-video-peer", recorder, coupled, video=True)
    old_audio_peer = _CoupledOldPeer("old-audio-peer", recorder, coupled, video=False)
    hold_video = _FakeHoldPad("hold-video", recorder)
    hold_audio = _FakeHoldPad("hold-audio", recorder)
    old_element = _FakeOldElement("caption-av-old-leg", recorder)
    old_role_video = object()
    old_role_audio = object()
    old_role_elements = [object()]
    engine.selector_sink_pads[0] = old_role_video
    engine.audio_sink_pads[0] = old_role_audio
    engine._source_leg_elements[0] = old_role_elements
    results: list[tuple[bool, str | None]] = []
    settled = threading.Event()

    def _settled(committed: bool, reason: str | None) -> None:
        results.append((committed, reason))
        settled.set()

    pending: dict[str, Any] = {
        "txn_id": 26,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "switch_at_end_of_current": True,
        "old_leg_eos": True,
        "timeout_id": None,
        "defer_timeout_id": None,
        "probe_id": None,
        "new_video_pad": object(),
        "new_audio_pad": object(),
        "new_elements": [],
        "new_src_pads": [hold_video, hold_audio],
        "hold_probes": [(hold_video, "new-video-hold"), (hold_audio, "new-audio-hold")],
        "readiness_probes": [],
        "boundary_probes": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, old_video_peer),
        "old_audio_pad": _FakeOldPad("old-audio", recorder, old_audio_peer),
        "old_elements": [old_element],
        "rebase_new_leg": False,
        "on_settled": _settled,
        "commit_in_progress": False,
    }
    engine._pending_reload = pending

    assert engine._commit_reload() is False
    # Both setters returned and both holds were removed, but 1.28 has not yet
    # applied either pending switch. No role publication, old-leg fencing, or
    # retirement may happen from setter return alone.
    assert pending["replacement_released"] is True
    assert pending["selector_handoff_confirmed"] is False
    assert engine.selector_sink_pads[0] is old_role_video
    assert engine.audio_sink_pads[0] is old_role_audio
    assert engine._source_leg_elements[0] is old_role_elements
    assert not pending["retirement_start_event"].is_set()
    assert not any(call.startswith("add_probe:old-") for call in recorder.calls)
    assert not settled.is_set()

    video_selector.apply_pending()
    assert pending["selector_handoff_confirmed"] is False
    assert not pending["retirement_start_event"].is_set()
    assert not settled.is_set()

    audio_selector.apply_pending()
    assert settled.wait(1.0), "the coupled A/V reload never settled"

    calls = recorder.calls
    set_video = _index_of(calls, "video_sel.set_property:active-pad")
    set_audio = _index_of(calls, "audio_sel.set_property:active-pad")
    video_always_ok = _index_of(calls, "old-video.set_property:always-ok=True")
    audio_always_ok = _index_of(calls, "old-audio.set_property:always-ok=True")
    release_video = _index_of(calls, "remove_probe:hold-video:new-video-hold")
    release_audio = _index_of(calls, "remove_probe:hold-audio:new-audio-hold")
    apply_video = _index_of(calls, "video_sel.apply_pending")
    apply_audio = _index_of(calls, "audio_sel.apply_pending")
    first_fence = min(
        _index_of(calls, "add_probe:old-video-peer:7"),
        _index_of(calls, "add_probe:old-audio-peer:7"),
    )
    old_null = _index_of(calls, "set_state:caption-av-old-leg:NULL")
    assert max(video_always_ok, audio_always_ok) < min(set_video, set_audio), calls
    assert max(set_video, set_audio) < min(release_video, release_audio), calls
    assert max(release_video, release_audio) < min(apply_video, apply_audio), calls
    assert max(apply_video, apply_audio) < first_fence < old_null, calls
    assert results == [(True, None)]
    assert completed.wait(1.0), "commit watchdog did not finish after reload settlement"
    assert engine._pending_reload is None
    assert exit_calls == []
    assert calls.count("pipeline.remove:caption-av-old-leg") == 1
    assert not any(":8:" in call for call in calls), calls

    captured = capsys.readouterr()
    assert captured.err.count("stage=selector-handoff-confirmed") == 1
    assert captured.err.count("stage=old-tail-detached") == 1
    assert captured.err.count("stage=committed elements=0") == 1
    assert "commit did not finish" not in captured.err


def test_retiring_tail_release_loop_names_entry_and_every_released_stream(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Localize a future release-loop wedge to one stream, not just one method.

    The 18:48:11 wedge dump put the blocking retirement thread inside
    ``release_request_pad``, but the last stage line on that stderr was
    ``stage=selector-handoff-confirmed`` -- one step *before* the loop, so the
    surviving log could not say whether the loop was even entered. The loop
    prints its entry (with the streams it is about to release) and one line per
    release that actually returned, so a stall leaves the stalled stream's line
    missing and names it by elimination.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _OrderedStderr:
        """Interleave stderr lines into the same ordered log as the Gst calls.

        Text position inside captured stderr cannot prove the entry line
        preceded the release *call*; one shared ordered list can.
        """

        def write(self, text: str) -> int:
            stripped = text.strip()
            if stripped:
                recorder.calls.append(f"stderr:{stripped}")
            return len(text)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(sys, "stderr", _OrderedStderr())

    pending: dict[str, Any] = {
        "old_video_pad": _FakeOldPad("old-video", recorder, _FakePeer("old-video-peer", recorder)),
        "old_audio_pad": _FakeOldPad("old-audio", recorder, _FakePeer("old-audio-peer", recorder)),
        "old_elements": [],
        "old_tail_drop_probes": [],
    }

    ok, reason = engine._dispose_confirmed_old_leg(pending)

    assert ok is True
    assert reason is None
    calls = recorder.calls
    entered = _index_of(
        calls, "stderr:CTRL reload diagnostic: stage=retiring-tail-release-entered streams="
    )
    release_video = _index_of(calls, "video_sel.release_request_pad:old-video")
    release_audio = _index_of(calls, "audio_sel.release_request_pad:old-audio")
    released_video = _index_of(
        calls, "stderr:CTRL reload diagnostic: stage=retiring-tail-released stream=video"
    )
    released_audio = _index_of(
        calls, "stderr:CTRL reload diagnostic: stage=retiring-tail-released stream=audio"
    )
    detached = _index_of(calls, "stderr:CTRL reload diagnostic: stage=old-tail-detached")

    assert calls[entered].endswith("streams=video,audio"), calls
    assert entered < release_video < released_video, calls
    assert released_video < release_audio < released_audio, calls
    assert released_audio < detached, calls


def test_post_handoff_old_tail_fence_failure_is_reported_without_stopping_output(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-handoff fence failure is honest and still releases the replacement."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    engine.commit_timeout_s = 5.0
    monkeypatch.setattr(
        engine,
        "_arm_commit_watchdog",
        lambda: (types.SimpleNamespace(cancel=lambda: None), threading.Event()),
    )

    video_peer = _FakePeer("old-video-peer", recorder)
    audio_peer = _FakePeer("old-audio-peer", recorder, fail_mask=7)
    hold_video = _FakeHoldPad("hold-video", recorder)
    hold_audio = _FakeHoldPad("hold-audio", recorder)
    results: list[tuple[bool, str | None]] = []
    pending: dict[str, Any] = {
        "txn_id": 23,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "switch_at_end_of_current": True,
        "old_leg_eos": True,
        "timeout_id": None,
        "defer_timeout_id": None,
        "probe_id": None,
        "new_video_pad": object(),
        "new_audio_pad": object(),
        "new_elements": [],
        "new_src_pads": [hold_video, hold_audio],
        "hold_probes": [(hold_video, "new-video-hold"), (hold_audio, "new-audio-hold")],
        "readiness_probes": [],
        "boundary_probes": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, video_peer),
        "old_audio_pad": _FakeOldPad("old-audio", recorder, audio_peer),
        "old_elements": [],
        "rebase_new_leg": False,
        "on_settled": lambda committed, reason: results.append((committed, reason)),
        "commit_in_progress": False,
    }
    engine._pending_reload = pending

    assert engine._commit_reload() is False
    thread = engine._reload_commit_thread
    if thread is not None:
        thread.join(timeout=5.0)

    calls = recorder.calls
    video_drop_add = _index_of(calls, "add_probe:old-video-peer:7")
    release_video_hold = _index_of(calls, "remove_probe:hold-video:new-video-hold")
    release_audio_hold = _index_of(calls, "remove_probe:hold-audio:new-audio-hold")
    assert max(release_video_hold, release_audio_hold) < video_drop_add, calls
    assert not any(
        call == "remove_probe:old-video-peer:old-video-peer-probe-7" for call in calls
    ), calls
    assert not any("release_request_pad" in call for call in calls), calls
    assert not any(":8:" in call for call in calls), calls
    assert any(call.startswith("video_sel.set_property") for call in calls), calls
    assert any(call.startswith("audio_sel.set_property") for call in calls), calls
    assert results == [(False, "cleanup-failed")]
    assert engine._pending_reload is None


def test_first_selector_setter_failure_restores_current_leg(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed first selector mutation is still a fully rollbackable attempt."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    engine.commit_timeout_s = 5.0
    results: list[tuple[bool, str | None]] = []

    class _FailFirstSelector(_FakeSelector):
        def set_property(self, key: str, value: Any) -> None:
            super().set_property(key, value)
            raise RuntimeError("selector rejected handoff")

    engine.selector = _FailFirstSelector("video_sel", recorder)
    monkeypatch.setattr(
        engine,
        "_arm_commit_watchdog",
        lambda: (types.SimpleNamespace(cancel=lambda: None), threading.Event()),
    )
    old_peer = _FakePeer("old-video-peer", recorder)
    new_tail = _FakeHoldPad("new-video", recorder)
    engine._pending_reload = {
        "txn_id": 24,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "switch_at_end_of_current": True,
        "old_leg_eos": True,
        "timeout_id": None,
        "defer_timeout_id": None,
        "probe_id": None,
        "new_video_pad": object(),
        "new_audio_pad": None,
        "new_elements": [],
        "new_src_pads": [new_tail],
        "hold_probes": [(new_tail, "new-video-hold")],
        "readiness_probes": [],
        "boundary_probes": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, old_peer),
        "old_audio_pad": None,
        "old_elements": [],
        "rebase_new_leg": False,
        "on_settled": lambda committed, reason: results.append((committed, reason)),
        "commit_in_progress": False,
        **_tail_padding_state(),
    }

    assert engine._commit_reload() is False

    calls = recorder.calls
    attempted_switch = _index_of(calls, "video_sel.set_property:active-pad")
    always_ok = _index_of(calls, "old-video.set_property:always-ok=True")
    assert always_ok < attempted_switch, calls
    assert not any(call.startswith("add_probe:old-video-peer") for call in calls), calls
    assert not any(call.startswith("peer.unlink:") for call in calls), calls
    assert results == [(False, "commit-setup")]
    assert engine._pending_reload is None


def test_aborted_leg_releases_fenced_selector_requests_before_null(engine_module) -> None:
    """A never-selected leg must be disconnected before a blocking NULL call.

    Its source fence prevents a late buffer/event from entering the selector.
    Request-pad release owns deactivation/unlinking; no connected-tail flush
    may interact with the on-air selector or another replacement's preroll.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video = _FakeOldPad("aborted-video", recorder, _FakePeer("video-tail", recorder))
    audio = _FakeOldPad("aborted-audio", recorder, _FakePeer("audio-tail", recorder))
    assert engine._dispose_source_leg(video, audio, [_FakeOldElement("aborted", recorder)]) == (
        True,
        None,
    )
    calls = recorder.calls
    null = _index_of(calls, "set_state:aborted:NULL")
    release_video = _index_of(calls, "video_sel.release_request_pad:aborted-video")
    release_audio = _index_of(calls, "audio_sel.release_request_pad:aborted-audio")
    assert _index_of(calls, "add_probe:video-tail:") < release_video < null
    assert _index_of(calls, "add_probe:audio-tail:") < release_audio < null
    assert not any("push_event" in call or "send_event" in call for call in calls)
    assert not any("set_property:active-pad" in call for call in calls)


def test_dispose_source_leg_is_best_effort_on_a_disposal_hiccup(engine_module) -> None:
    """A disposal failure must be swallowed (logged), never raised -- a reload
    disposal hiccup must not be able to kill a live channel (pre-existing
    contract)."""

    class _RaisingElement:
        def set_state(self, _state: Any) -> None:
            raise RuntimeError("boom")

    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.selector = None
    engine.audio_selector = None

    # Must not raise, but must also not misreport the partial cleanup as success.
    ok, reason = engine._dispose_source_leg(None, None, [_RaisingElement()])
    assert ok is False
    assert reason is not None and "element-null-error:1" in reason


# --- (3) the staged diagnostic log lines fire, in order -------------------------


def test_commit_prints_the_staged_log_lines_in_order(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep stdout's settlement contract and mirror distinct stages to stderr.

    Sandbox verdict parsing consumes ``CTRL reload committed`` from stdout.  The
    daemon captures stderr independently, so distinct diagnostic stages go there
    without moving or duplicating the settlement marker that existing evidence
    consumers count.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    pending: dict[str, Any] = {
        "timeout_id": None,
        "defer_timeout_id": None,
        "new_video_pad": object(),
        "new_audio_pad": None,
        "rebase_new_leg": False,
        "hold_probes": [],
        "boundary_probes": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, peer=None),
        "old_audio_pad": None,
        "old_elements": [],
        "new_elements": [],
        "on_settled": None,
    }

    _complete_commit_for_test(engine, pending)
    captured = capsys.readouterr()
    out = captured.out

    markers = (
        "CTRL reload: switching selector",
        "CTRL reload: holds released",
        "CTRL reload: old leg disposed",
        "CTRL reload committed",
    )
    positions = [out.index(marker) for marker in markers]
    assert positions == sorted(positions), f"staged log lines out of order; output={out!r}"
    diagnostic_markers = (
        "CTRL reload diagnostic: stage=switching-selector",
        "CTRL reload diagnostic: stage=holds-released",
        "CTRL reload diagnostic: stage=selector-handoff-confirmed",
        "CTRL reload diagnostic: stage=old-leg-disposed",
        "CTRL reload diagnostic: stage=committed elements=0",
    )
    diagnostic_positions = [captured.err.index(marker) for marker in diagnostic_markers]
    assert diagnostic_positions == sorted(diagnostic_positions), captured.err
    assert "CTRL reload committed" not in captured.err


# --- (4) asynchronous transaction ownership and failure settlement --------------


def test_selector_isolation_queue_uses_explicit_bounded_nonleaky_defaults(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _Queue:
        def set_property(self, key: str, value: object) -> None:
            recorder.calls.append(f"{key}={value}")

    queue = _Queue()
    engine._make = lambda _spec: queue  # type: ignore[method-assign]

    assert engine._make_selector_isolation_queue("isolation") is queue
    assert recorder.calls == [
        "max-size-buffers=200",
        "max-size-bytes=10485760",
        "max-size-time=1000000000",
        "leaky=0",
    ]


def test_selector_uses_explicit_two_phase_continuity_properties(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _Selector:
        def set_property(self, key: str, value: object) -> None:
            recorder.calls.append(f"{key}={value}")

    selector = _Selector()
    engine._make = lambda _spec: selector  # type: ignore[method-assign]

    assert engine._make_selector("program-selector") is selector
    assert recorder.calls == [
        "sync-streams=True",
        "sync-mode=0",
        "cache-buffers=False",
        "drop-backwards=True",
    ]


def test_finalizer_ignores_a_stale_transaction_identity(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    callbacks: list[tuple[bool, str | None]] = []
    stale = {
        "commit_in_progress": True,
        "retirement_result": (True, None),
        "on_settled": lambda committed, reason: callbacks.append((committed, reason)),
    }
    current = {"commit_in_progress": False}
    engine._pending_reload = current

    assert engine._finish_reload_commit(stale) is False
    assert engine._pending_reload is current
    assert callbacks == []
    assert recorder.calls == []


def test_reload_during_commit_is_rejected_before_any_graph_mutation(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    current = {"commit_in_progress": True}
    engine._pending_reload = current
    observed: list[tuple[bool, str | None]] = []
    build_calls: list[object] = []
    engine._instantiate_source_leg = lambda leg: build_calls.append(leg)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="reload commit already in progress"):
        engine.reload_program(
            object(), on_settled=lambda committed, reason: observed.append((committed, reason))
        )

    assert engine._pending_reload is current
    assert build_calls == []
    assert observed == []
    assert recorder.calls == []


def test_success_callback_reentry_can_start_a_new_reload(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    current_results: list[tuple[bool, str | None]] = []

    def _current_settled(committed: bool, reason: str | None) -> None:
        current_results.append((committed, reason))
        engine._pending_reload = {"newer": True}

    pending = {
        # Production pending dicts always carry the txn id (engine._begin_reload);
        # _finish_reload_commit now records it as the stall diagnostic's reload
        # context on the committed path, exactly as the cleanup path already did.
        "txn_id": 1,
        "commit_in_progress": True,
        "retirement_result": (True, None),
        "boundary_probes": [],
        "hold_probes": [],
        "on_settled": _current_settled,
        "commit_completed": None,
        "commit_watchdog": None,
    }
    engine._pending_reload = pending
    assert engine._finish_reload_commit(pending) is False
    assert current_results == [(True, None)]
    assert engine._reload_context == (1, "committed")
    assert engine._pending_reload == {"newer": True}


def test_disposal_state_failure_is_not_a_successful_cleanup(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _FailedElement(_FakeOldElement):
        def set_state(self, state: Any) -> str:
            super().set_state(state)
            return _FakeStateChangeReturn.FAILURE

    ok, reason = engine._dispose_source_leg(
        _FakeOldPad("old-video", recorder, peer=None), None, [_FailedElement("bad", recorder)]
    )

    assert ok is False
    assert reason is not None and "element-null-incomplete:1:FAILURE" in reason
    assert "pipeline.remove:bad" in recorder.calls


def test_disposal_async_that_settles_to_null_is_a_successful_retirement(engine_module) -> None:
    """Round-2 finding 3: ASYNC on a downward transition is legitimate, not a failure.

    Source legs are bins, and a bin winding its children down answers ASYNC by
    GStreamer contract. Reporting that as "incomplete cleanup" is what made an
    ordinary retirement look fatal to the commit path. The bounded ``get_state``
    wait is the correct reading; only a leg that never reaches NULL fails."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _AsyncElement(_FakeOldElement):
        def set_state(self, state: Any) -> str:
            super().set_state(state)
            return _FakeStateChangeReturn.ASYNC

    ok, reason = engine._dispose_source_leg(None, None, [_AsyncElement("async", recorder)])

    assert ok is True
    assert reason is None
    assert any(call.startswith("get_state:async:") for call in recorder.calls), recorder.calls


def test_disposal_async_that_never_settles_is_retried_once_then_reported(engine_module) -> None:
    """The bounded wait is bounded: a leg still not at NULL after a retry fails."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _WedgedElement(_FakeOldElement):
        def set_state(self, state: Any) -> str:
            super().set_state(state)
            return _FakeStateChangeReturn.ASYNC

        def get_state(self, timeout: Any) -> tuple[str, str, str]:
            self.recorder.calls.append(f"get_state:{self.name}:{timeout}")
            return (_FakeStateChangeReturn.ASYNC, "PAUSED", _FakeState.NULL)

    ok, reason = engine._dispose_source_leg(None, None, [_WedgedElement("wedged", recorder)])

    assert ok is False
    assert reason is not None and "element-null-incomplete:1:async-unsettled:ASYNC" in reason
    # One retry, not an unbounded loop: two set_state attempts, two bounded waits.
    assert sum(call.startswith("set_state:wedged:") for call in recorder.calls) == 2
    assert sum(call.startswith("get_state:wedged:") for call in recorder.calls) == 2


def test_disposal_unlink_and_remove_problems_are_warnings_not_failures(engine_module) -> None:
    """Round-2 finding 3: by the time unlink/remove runs the leg is already at NULL
    and off air, so a hiccup there costs bookkeeping, not airtime. It must not be
    escalated into a retirement failure that takes a producing channel down."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _RefusingPipeline(_FakePipeline):
        def remove(self, element: Any) -> bool:
            super().remove(element)
            return False

    engine.pipeline = _RefusingPipeline(recorder)

    ok, reason = engine._dispose_source_leg(None, None, [_FakeOldElement("leg", recorder)])

    assert ok is True
    assert reason is None


def test_cleanup_failure_settles_false_but_keeps_a_producing_channel_on_air(
    engine_module,
) -> None:
    """Round-2 finding 3: an incomplete retirement is reported honestly on the
    receipt, but it no longer quits the loop. The replacement leg is already
    selected and feeding the mux; killing the channel over a leftover element took
    a PRODUCING channel off air. A retirement problem that genuinely stops output
    is the stall watchdog's to escalate, with far better evidence."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    results: list[tuple[bool, str | None]] = []

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    loop = _Loop()
    engine._loop = loop
    pending = {
        "txn_id": 1,
        "commit_in_progress": True,
        "retirement_result": (False, "element-null-failed:2"),
        "boundary_probes": [],
        "hold_probes": [],
        "on_settled": lambda committed, reason: results.append((committed, reason)),
        "commit_completed": None,
        "commit_watchdog": None,
    }
    engine._pending_reload = pending

    assert engine._finish_reload_commit(pending) is False
    assert results == [(False, "cleanup-failed")]
    assert engine._reload_context == (1, "cleanup-failed")
    assert engine._pending_reload is None
    assert engine._stopping is False
    assert engine._error is None
    assert loop.quit_called is False


def test_new_active_leg_bus_error_during_retirement_is_fatal(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    source = object()
    engine._pending_reload = {
        "commit_in_progress": True,
        "new_elements": [source],
    }

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    class _Message:
        type = _FakeMessageType.ERROR
        src = source

        @staticmethod
        def parse_error() -> tuple[str, str]:
            return "active failure", "debug"

    engine._loop = _Loop()
    engine._abort_pending_reload = lambda _reason: pytest.fail(  # type: ignore[method-assign]
        "already-selected replacement must not be aborted as uncommitted"
    )

    assert engine._on_bus(None, _Message()) is True
    assert engine._error == ("active failure", "debug")
    assert engine._loop.quit_called is True


def test_retiring_old_leg_bus_error_after_selector_handoff_is_contained(
    engine_module, capsys
) -> None:
    """A finite old playlist may surface FLOW_ERROR while being detached.

    The selector already points at the replacement, so an error proven to come
    from a descendant of the retiring leg must not kill the producing worker.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    old_element = _FakeOldElement("old-program", recorder)

    class _OldDescendant:
        @staticmethod
        def get_name() -> str:
            return "old-tsdemux"

        @staticmethod
        def get_parent() -> _FakeOldElement:
            return old_element

    source = _OldDescendant()
    engine._pending_reload = {
        "commit_in_progress": True,
        "selector_handoff_started": True,
        "selector_handoff_confirmed": True,
        "new_elements": [object()],
        "old_elements": [old_element],
    }

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    class _Message:
        type = _FakeMessageType.ERROR
        src = source

        @staticmethod
        def parse_error() -> tuple[str, str]:
            return "streaming stopped, reason error (-5)", "debug"

    engine._loop = _Loop()

    assert engine._on_bus(None, _Message()) is True
    assert engine._error is None
    assert engine._loop.quit_called is False
    assert (
        "contained retiring old-leg error after confirmed selector handoff"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("source_kind", "selector_handoff_confirmed"),
    [("old", False), ("new", True), ("shared", True)],
)
def test_bus_error_outside_retiring_post_handoff_leg_remains_fatal(
    engine_module, source_kind: str, selector_handoff_confirmed: bool
) -> None:
    """Old pre-handoff, selected-new, and shared errors still recover worker."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    old_source = _FakeOldElement("old-program", recorder)
    new_source = _FakeOldElement("new-program", recorder)
    source = {
        "old": old_source,
        "new": new_source,
        "shared": _FakeOldElement("shared-mux", recorder),
    }[source_kind]
    engine._pending_reload = {
        "commit_in_progress": True,
        "selector_handoff_started": True,
        "selector_handoff_confirmed": selector_handoff_confirmed,
        "new_elements": [new_source],
        "old_elements": [old_source],
    }

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    class _Message:
        type = _FakeMessageType.ERROR
        src = source

        @staticmethod
        def parse_error() -> tuple[str, str]:
            return "fatal stream error", "debug"

    engine._loop = _Loop()

    assert engine._on_bus(None, _Message()) is True
    assert engine._error == ("fatal stream error", "debug")
    assert engine._loop.quit_called is True


def test_retirement_thread_start_failure_aborts_before_selector_switch(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    engine.commit_timeout_s = 1.0
    results: list[tuple[bool, str | None]] = []
    new_pad = _FakeOldPad("new-video", recorder, peer=None)
    engine._pending_reload = {
        "txn_id": 1,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "switch_at_end_of_current": False,
        "old_leg_eos": True,
        "timeout_id": None,
        "defer_timeout_id": None,
        "probe_id": None,
        "new_video_pad": new_pad,
        "new_audio_pad": None,
        "new_elements": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, peer=None),
        "old_audio_pad": None,
        "old_elements": [],
        "hold_probes": [],
        "boundary_probes": [],
        "on_settled": lambda committed, reason: results.append((committed, reason)),
        "commit_in_progress": False,
        **_tail_padding_state(),
    }

    class _NoStartThread:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(
        engine,
        "_arm_commit_watchdog",
        lambda: (types.SimpleNamespace(cancel=lambda: None), threading.Event()),
    )
    monkeypatch.setattr(engine_module.threading, "Thread", _NoStartThread)

    assert engine._commit_reload() is False
    assert results == [(False, "commit-thread-start")]
    assert engine._pending_reload is None
    assert not any(call.startswith("video_sel.set_property") for call in recorder.calls)


def test_commit_watchdog_start_failure_cancels_retirement_and_aborts_before_switch(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    results: list[tuple[bool, str | None]] = []
    new_pad = _FakeOldPad("new-video", recorder, peer=None)
    engine._pending_reload = {
        "txn_id": 1,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "switch_at_end_of_current": False,
        "old_leg_eos": True,
        "timeout_id": None,
        "defer_timeout_id": None,
        "probe_id": None,
        "new_video_pad": new_pad,
        "new_audio_pad": None,
        "new_elements": [],
        "hold_probes": [],
        "boundary_probes": [],
        "on_settled": lambda committed, reason: results.append((committed, reason)),
        "commit_in_progress": False,
        **_tail_padding_state(),
    }
    monkeypatch.setattr(
        engine,
        "_arm_commit_watchdog",
        lambda: (_ for _ in ()).throw(RuntimeError("timer unavailable")),
    )

    assert engine._commit_reload() is False
    assert results == [(False, "commit-watchdog-start")]
    assert engine._pending_reload is None
    assert engine._reload_commit_thread is None
    assert not any(call.startswith("video_sel.set_property") for call in recorder.calls)


def test_stop_settles_current_callback_once(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    engine.audio_tap_writer = None
    current: list[tuple[bool, str | None]] = []

    class _StoppedThread:
        def join(self, timeout: float) -> None:
            assert 0 <= timeout <= 0.1

        @staticmethod
        def is_alive() -> bool:
            return False

    class _StopPipeline(_FakePipeline):
        def set_state(self, state: Any) -> str:
            assert state == _FakeState.NULL
            return _FakeStateChangeReturn.SUCCESS

        def get_state(self, _timeout: int) -> tuple[str, None, None]:
            return _FakeStateChangeReturn.SUCCESS, None, None

    engine.pipeline = _StopPipeline(recorder)
    engine._reload_commit_thread = _StoppedThread()
    engine._pending_reload = {
        "txn_id": 1,
        "commit_in_progress": True,
        "selector_handoff_confirmed": True,
        "selector_notify_handlers": [],
        "retirement_result": (True, None),
        "boundary_probes": [],
        "hold_probes": [],
        "on_settled": lambda committed, reason: current.append((committed, reason)),
        "commit_completed": None,
        "commit_watchdog": None,
    }
    assert engine.stop(force_exit_on_hang=False) is True
    assert current == [(False, "stopped")]
    assert engine._pending_reload is None


def test_stop_cancels_requested_commit_before_selector_handoff_confirmation(
    engine_module,
) -> None:
    """A queued selector notification cannot start retirement after shutdown."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.1
    engine.audio_tap_writer = None
    current: list[tuple[bool, str | None]] = []
    old_peer = _FakePeer("old-video-peer", recorder)
    new_tail = _FakeHoldPad("new-video", recorder)
    completed = threading.Event()

    class _StopPipeline(_FakePipeline):
        def set_state(self, state: Any) -> str:
            assert state == _FakeState.NULL
            return _FakeStateChangeReturn.SUCCESS

        def get_state(self, _timeout: int) -> tuple[str, None, None]:
            return _FakeStateChangeReturn.SUCCESS, None, None

    engine.pipeline = _StopPipeline(recorder)
    engine._pending_reload = {
        "txn_id": 25,
        "commit_in_progress": True,
        "selector_handoff_started": True,
        "selector_handoff_confirmed": False,
        "handoff_started": True,
        "retirement_cancelled": False,
        "retirement_result": None,
        "retirement_start_event": threading.Event(),
        "old_tail_drop_probes": [(old_peer, "old-video-peer-probe-7")],
        "selector_notify_handlers": [],
        "probe_id": None,
        "readiness_probes": [],
        "boundary_probes": [],
        "new_video_pad": object(),
        "new_src_pads": [new_tail],
        "hold_probes": [(new_tail, "new-video-hold")],
        "timeout_id": None,
        "defer_timeout_id": None,
        "on_settled": lambda committed, reason: current.append((committed, reason)),
        "commit_completed": completed,
        "commit_watchdog": types.SimpleNamespace(cancel=lambda: None),
    }

    assert engine.stop(force_exit_on_hang=False) is True
    assert current == [(False, "stopped")]
    assert completed.is_set()
    assert engine._pending_reload is None
    assert engine._stopping is True
    assert not any(call.startswith("video_sel.set_property") for call in recorder.calls)
    assert recorder.calls.count("remove_probe:old-video-peer:old-video-peer-probe-7") == 1, (
        recorder.calls
    )


def test_stop_bounds_a_pipeline_set_state_null_call_that_blocks(engine_module) -> None:
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.teardown_timeout_s = 0.02
    engine.audio_tap_writer = None
    entered = threading.Event()
    release = threading.Event()
    returned: list[bool] = []

    class _BlockingStopPipeline(_FakePipeline):
        def set_state(self, state: Any) -> str:
            assert state == _FakeState.NULL
            entered.set()
            release.wait(1.0)
            return _FakeStateChangeReturn.SUCCESS

        def get_state(self, _timeout: int) -> tuple[str, None, None]:
            return _FakeStateChangeReturn.SUCCESS, None, None

    engine.pipeline = _BlockingStopPipeline(recorder)
    caller = threading.Thread(
        target=lambda: returned.append(engine.stop(force_exit_on_hang=False)), daemon=True
    )
    caller.start()
    try:
        assert entered.wait(0.2)
        caller.join(0.2)
        assert not caller.is_alive(), "stop() exceeded its bound inside set_state(NULL)"
        assert returned == [False]
    finally:
        release.set()
        caller.join(0.2)


# --- (5) the commit watchdog THREAD escapes a commit that never returns ---------


def test_commit_watchdog_force_exits_when_the_commit_never_returns(
    engine_module, monkeypatch
) -> None:
    """Item 85's escape hatch: if ``_commit_reload`` (simulated here by simply
    never calling ``watchdog.cancel()``, i.e. a commit that hangs forever and
    never returns control) does not finish within ``commit_timeout_s``, the
    watchdog THREAD -- not a GLib timeout source, which could never fire if
    the SAME thread that would run it is the one wedged -- must fire on its
    own, independent OS thread: dump every live thread's Python stack FIRST
    (the actual diagnostic this item ships), print the diagnosis, and
    force-exit IMMEDIATELY with the distinct
    ``GST_RELOAD_COMMIT_TIMEOUT_EXIT_CODE`` -- with NO pipeline teardown
    attempt in between (a downward state transition takes the same
    STREAM_LOCK a wedged thread already holds; attempting it here would
    either do nothing or wedge this watchdog thread too). ``os._exit`` and
    ``faulthandler.dump_traceback`` are both monkeypatched (calling the real
    ``os._exit`` would kill the test process) -- both are recorded instead,
    proving they were reached, in the right order, without actually
    exiting."""
    from civiccast.egress.gst.exit_codes import GST_RELOAD_COMMIT_TIMEOUT_EXIT_CODE

    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.commit_timeout_s = 0.05
    # Must NOT be called: proves the watchdog attempts no pipeline teardown.
    engine.pipeline.set_state = lambda state: recorder.calls.append(  # type: ignore[attr-defined]
        f"pipeline.set_state:{state}"
    )
    dump_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        engine_module.faulthandler,
        "dump_traceback",
        lambda **kwargs: dump_calls.append(kwargs),
    )
    exit_calls: list[int] = []
    monkeypatch.setattr(engine_module.os, "_exit", exit_calls.append)

    watchdog, completed = engine._arm_commit_watchdog()
    # A commit that never returns never sets `completed` and never calls
    # watchdog.cancel() -- exactly the condition this thread exists to escape.
    # Join (bounded) rather than sleep: the thread fires as soon as its own
    # timer elapses, not on this test's clock.
    assert not completed.is_set()
    watchdog.join(timeout=5.0)

    assert not watchdog.is_alive(), "commit watchdog thread never fired"
    assert len(dump_calls) == 1
    assert dump_calls[0].get("all_threads") is True
    assert exit_calls == [int(GST_RELOAD_COMMIT_TIMEOUT_EXIT_CODE)]
    assert not any(call.startswith("pipeline.set_state:") for call in recorder.calls), (
        "watchdog must not attempt any pipeline teardown before force-exiting"
    )


def test_commit_watchdog_is_a_no_op_when_the_commit_finishes_in_time(
    engine_module, monkeypatch
) -> None:
    """The normal case: a commit that finishes well within ``commit_timeout_s``
    cancels the watchdog and the process is never touched."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.commit_timeout_s = 5.0
    engine._pending_reload = {
        "txn_id": 1,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "switch_at_end_of_current": False,
        "old_leg_eos": True,
        "timeout_id": None,
        "defer_timeout_id": None,
        "new_video_pad": object(),
        "new_audio_pad": None,
        "rebase_new_leg": False,
        "hold_probes": [],
        "boundary_probes": [],
        "old_video_pad": _FakeOldPad("old-video", recorder, peer=None),
        "old_audio_pad": None,
        "old_elements": [],
        "new_elements": [],
        "on_settled": None,
    }
    exit_calls: list[int] = []
    monkeypatch.setattr(engine_module.os, "_exit", exit_calls.append)
    dump_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        engine_module.faulthandler,
        "dump_traceback",
        lambda **kwargs: dump_calls.append(kwargs),
    )

    result = engine._commit_reload()
    retirement = engine._reload_commit_thread
    if retirement is not None:
        retirement.join(timeout=1.0)

    assert result is False
    assert retirement is None or not retirement.is_alive()
    assert engine._pending_reload is None
    assert exit_calls == [], "a commit that finished in time must never force-exit"
    assert dump_calls == [], "a commit that finished in time must never dump a stack trace"


def test_commit_watchdog_completion_flag_wins_a_cancel_race(engine_module, monkeypatch) -> None:
    """Hostile-review follow-up: ``Timer.cancel()`` alone cannot close the race
    where the watchdog thread has ALREADY started running
    ``_on_commit_wedged`` (past ``Timer.cancel()``'s own internal check) at the
    exact moment the commit finishes -- cancelling at that point is a no-op.
    Simulates that exact race directly: fire ``_on_commit_wedged`` (obtained
    via the real ``threading.Timer.function`` the production code built) AFTER
    ``completed`` is set but BEFORE/without ever calling ``cancel()`` at all --
    proving the function itself, not the ``Timer`` API, is what refuses to
    exit once the commit is known to have finished."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.commit_timeout_s = 5.0  # never actually waited for the timer to fire
    exit_calls: list[int] = []
    monkeypatch.setattr(engine_module.os, "_exit", exit_calls.append)
    dump_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        engine_module.faulthandler,
        "dump_traceback",
        lambda **kwargs: dump_calls.append(kwargs),
    )

    watchdog, completed = engine._arm_commit_watchdog()
    try:
        # Simulates the commit finishing and _commit_reload's finally block
        # setting `completed` BEFORE cancel() -- exactly the documented order.
        completed.set()
        watchdog.cancel()
        # Even if the timer thread had ALREADY passed cancel()'s own check
        # (the race this flag exists for), invoking the underlying function
        # directly -- exactly what that thread would do next -- must still be
        # a no-op now that `completed` is set.
        watchdog.function()
    finally:
        watchdog.join(timeout=5.0)  # bounded: never actually fires (5s interval)

    assert exit_calls == [], "a completed commit must never force-exit, even racing cancel()"
    assert dump_calls == [], (
        "a completed commit must never dump a stack trace, even racing cancel()"
    )


# --- (6) commit_timeout_s validation/clamping -----------------------------------


def test_resolve_commit_timeout_s_clamps_and_warns_on_an_out_of_range_value(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unlike an earlier draft's inline ``max(1.0, self.commit_timeout_s)``
    (a SILENT floor), an out-of-bounds ``commit_timeout_s`` is clamped WITH a
    stderr warning naming the value and the bound it was clamped to."""
    resolved = engine_module._resolve_commit_timeout_s(0.001)
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert resolved == engine_module._MIN_COMMIT_TIMEOUT_S
    assert "0.001" in out
    assert "clamped" in out


def test_resolve_commit_timeout_s_accepts_an_in_range_value_silently(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved = engine_module._resolve_commit_timeout_s(20.0)
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert resolved == 20.0
    assert out == ""


def test_resolve_commit_timeout_s_falls_back_to_default_on_nan(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved = engine_module._resolve_commit_timeout_s(float("nan"))
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert resolved == engine_module._DEFAULT_COMMIT_TIMEOUT_S
    assert "non-finite" in out


# --- (5) round-2 finding 1: the abort path's contract ---------------------------


def _abort_pending(recorder: _Recorder, pads: list[Any], elements: list[Any]) -> dict[str, Any]:
    return {
        "txn_id": 1,
        "probe_id": None,
        "timeout_id": None,
        "defer_timeout_id": None,
        "boundary_probes": [],
        "hold_probes": [(pad, f"{pad.name}-hold") for pad in pads],
        "new_src_pads": list(pads),
        "new_video_pad": None,
        "new_audio_pad": None,
        "new_elements": elements,
        "commit_in_progress": False,
        "on_settled": None,
        **_tail_padding_state(),
    }


def _tail_padding_state() -> dict[str, Any]:
    """Lifecycle fields present on every production pending reload transaction."""
    return {
        "tail_padding_done": True,
        "tail_padding_started": False,
        "tail_padding_stop": threading.Event(),
        "tail_padding_wake": threading.Event(),
        "tail_padding_start_id": None,
        "tail_padding_thread": None,
    }


def test_abort_drops_the_leg_at_its_own_pads_before_lifting_the_hold(engine_module) -> None:
    """Round-2 finding 1: DETACH BEFORE RELEASE.

    Lifting a held leg's blocking probes frees its streaming threads. Until this
    fix those threads ran straight into the input-selector on a pad that was still
    INACTIVE -- a state ``_SELECTOR_PROPS`` explicitly documents the deferred
    switch as never producing ("pushes nothing at all while inactive"). The DROP
    probe must therefore be installed on the leg's own src pads FIRST, so the
    ordering is structural and not a race."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = [_FakeHoldPad("new-video", recorder), _FakeHoldPad("new-audio", recorder)]
    pending = _abort_pending(recorder, pads, [_FakeOldElement("aborted", recorder)])
    engine._pending_reload = pending

    engine._abort_pending_reload("error")
    for thread in engine._abort_retire_threads:
        thread.join(timeout=5.0)

    drops = [i for i, call in enumerate(recorder.calls) if call.startswith("add_probe:")]
    releases = [i for i, call in enumerate(recorder.calls) if call.startswith("remove_probe:")]
    assert len(drops) == 2, recorder.calls
    assert len(releases) == 2, recorder.calls
    assert max(drops) < min(releases), recorder.calls
    # The probe installed is the drop-everything one, not some other callback.
    assert all("_drop_everything_probe" in recorder.calls[i] for i in drops), recorder.calls
    assert engine._pending_reload is None


def test_abort_retires_the_leg_off_the_main_loop(engine_module) -> None:
    """Round-2 finding 1: ``set_state(NULL)`` blocks until the leg's streaming
    threads join. ``_abort_pending_reload`` runs ON the GLib main loop, so an
    errored leg that will not go down would take the loop -- and with it both
    watchdogs and the control-plane reader -- off a channel that is still on air.
    Retirement therefore happens on a worker thread, and its result is reported
    rather than discarded."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    main_thread = threading.current_thread()
    disposing_threads: list[Any] = []

    class _ThreadRecordingElement(_FakeOldElement):
        def set_state(self, state: Any) -> str:
            disposing_threads.append(threading.current_thread())
            return super().set_state(state)

    pending = _abort_pending(
        recorder,
        [_FakeHoldPad("new-video", recorder)],
        [_ThreadRecordingElement("aborted", recorder)],
    )
    engine._pending_reload = pending

    engine._abort_pending_reload("error")
    assert len(engine._abort_retire_threads) == 1
    for thread in engine._abort_retire_threads:
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "abort retirement thread did not finish"

    assert disposing_threads, "the leg was never disposed"
    assert all(thread is not main_thread for thread in disposing_threads), (
        "abort disposal ran on the main loop"
    )


def test_abort_settles_the_caller_without_waiting_for_retirement(engine_module) -> None:
    """The honest ack (item 4) reports the reload's OUTCOME, which is already
    decided; it is not held behind cleanup."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    results: list[tuple[bool, str | None]] = []
    pending = _abort_pending(
        recorder, [_FakeHoldPad("new-video", recorder)], [_FakeOldElement("aborted", recorder)]
    )
    pending["on_settled"] = lambda committed, reason: results.append((committed, reason))
    engine._pending_reload = pending

    engine._abort_pending_reload("timeout")
    for thread in engine._abort_retire_threads:
        thread.join(timeout=5.0)

    assert results == [(False, "timeout")]


# --- (5b) item 3: an aborted leg's own errors must not exit the worker ----------
#
# ``_abort_pending_reload`` clears ``self._pending_reload`` the instant it decides
# not to use the new leg -- but the leg is NOT gone: its disposal runs on a worker
# thread. In that window both attributing predicates early-out on
# ``_pending_reload is None``, so a second bus ERROR from the aborted leg's own
# elements matched no containment branch and fell through to the fatal tail.
# Observed on the education channel, ``gst-worker.stdout.log`` lines 2649-2650:
# the pre-commit abort on ``source=decodebin14`` is contained, and the very next
# line is the worker's ``WORKER_RESULT`` carrying that same decodebin's GError --
# a producing channel taken off air by the teardown of the leg the engine had just
# rejected. The leg is DROP-fenced at its own src pads before the holds are lifted
# (``_detach_leg_from_selectors``), so nothing it emits can reach the on-air path.


def _engine_aborting_a_wedged_leg(
    engine_module, recorder: _Recorder, *, name: str = "decodebin14"
) -> tuple[Any, Any, threading.Event]:
    """Abort a pre-commit reload whose leg wedges in ``set_state(NULL)``.

    A wedged disposal is the deterministic way to hold the window open: the
    retirement thread is inside ``_dispose_source_leg`` while the aborted leg's
    elements are still live on the bus, which is exactly the state item 3 found.
    Returns ``(engine, aborted_leg_element, release)``; the caller MUST set
    ``release`` (and join) so no test leaves a thread parked in ``set_state``."""
    engine = _bare_engine_for_commit(engine_module, recorder)
    entered = threading.Event()
    release = threading.Event()

    class _WedgedElement(_FakeOldElement):
        def set_state(self, state: Any) -> str:
            entered.set()
            release.wait(timeout=5.0)
            return super().set_state(state)

    element = _WedgedElement(name, recorder)
    engine._pending_reload = _abort_pending(recorder, [], [element])
    engine._abort_pending_reload("error")
    assert entered.wait(timeout=5.0), "the aborted leg's retirement never started"
    assert engine._pending_reload is None
    return engine, element, release


def test_second_error_from_the_just_aborted_leg_is_contained(engine_module, capsys) -> None:
    """Item 3, the observed shape: the abort is contained (line 2649) and then the
    SAME element's second error decides the worker's fate (line 2650)."""
    recorder = _Recorder()
    engine, element, release = _engine_aborting_a_wedged_leg(engine_module, recorder)

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    class _Message:
        type = _FakeMessageType.ERROR
        src = element

        @staticmethod
        def parse_error() -> tuple[str, str]:
            return (
                "GStreamer encountered a general stream error.",
                "all streams without buffers",
            )

    engine._loop = _Loop()
    try:
        assert engine._on_bus(None, _Message()) is True
        assert engine._error is None, "an aborted leg's error took the channel off air"
        assert engine._loop.quit_called is False
        assert "contained error from an already-aborted reload leg" in capsys.readouterr().out
    finally:
        release.set()
        for thread in engine._abort_retire_threads:
            thread.join(timeout=5.0)


def test_second_error_from_inside_the_just_aborted_leg_is_contained(engine_module) -> None:
    """A decodebin-internal decoder posts with the INNER element as its source, so
    attribution has to walk parents -- the same walk the two pre-existing
    predicates do."""
    recorder = _Recorder()
    engine, element, release = _engine_aborting_a_wedged_leg(engine_module, recorder)

    class _AbortedLegDescendant:
        @staticmethod
        def get_name() -> str:
            return "avdec_h264-inside-the-aborted-leg"

        @staticmethod
        def get_parent() -> Any:
            return element

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    class _Message:
        type = _FakeMessageType.ERROR
        src = _AbortedLegDescendant()

        @staticmethod
        def parse_error() -> tuple[str, str]:
            return "decoder failure inside the aborted leg", "debug"

    engine._loop = _Loop()
    try:
        assert engine._on_bus(None, _Message()) is True
        assert engine._error is None
        assert engine._loop.quit_called is False
    finally:
        release.set()
        for thread in engine._abort_retire_threads:
            thread.join(timeout=5.0)


def test_second_error_from_detached_child_of_aborted_leg_is_contained(engine_module) -> None:
    """Bus delivery may follow teardown detaching a decodebin child from its bin."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    entered = threading.Event()
    release = threading.Event()

    class _Child:
        parent: Any = None

        @staticmethod
        def get_name() -> str:
            return "tsdemux7"

        def get_parent(self) -> Any:
            return self.parent

    class _NestedBin(_FakeOldElement):
        def __init__(self) -> None:
            super().__init__("decodebin7", recorder)
            self.child = _Child()
            self.child.parent = self

        def iterate_recurse(self) -> Any:
            class _Iterator:
                done = False

                def next(inner_self) -> tuple[str, Any]:
                    if inner_self.done:
                        return _FakeIteratorResult.DONE, None
                    inner_self.done = True
                    return _FakeIteratorResult.OK, self.child

            return _Iterator()

        def set_state(self, state: Any) -> str:
            # Model GstBin teardown removing the child while its already-posted
            # error is still queued for the engine's main-loop bus callback.
            self.child.parent = None
            entered.set()
            release.wait(timeout=5.0)
            return super().set_state(state)

    leg = _NestedBin()
    engine._pending_reload = _abort_pending(recorder, [], [leg])
    engine._abort_pending_reload("superseded")
    assert entered.wait(timeout=5.0), "the aborted leg's retirement never started"
    assert leg.child.get_parent() is None, "test child was not detached during retirement"

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    class _Message:
        type = _FakeMessageType.ERROR
        src = leg.child

        @staticmethod
        def parse_error() -> tuple[str, str]:
            return "detached decoder failed during abort", "debug"

    engine._loop = _Loop()
    try:
        assert engine._on_bus(None, _Message()) is True
        assert engine._error is None, (
            "a detached child from an aborted leg took the channel off air"
        )
        assert engine._loop.quit_called is False
    finally:
        release.set()
        for thread in engine._abort_retire_threads:
            thread.join(timeout=5.0)


def test_error_from_an_unrelated_element_is_still_fatal_while_an_abort_retires(
    engine_module,
) -> None:
    """The containment is attributed, not blanket: an error that is NOT the aborted
    leg's still follows the ordinary fatal path, so supervisor recovery stays
    truthful for a channel that has genuinely failed."""
    recorder = _Recorder()
    engine, _element, release = _engine_aborting_a_wedged_leg(engine_module, recorder)

    class _Loop:
        quit_called = False

        def quit(self) -> None:
            self.quit_called = True

    class _Message:
        type = _FakeMessageType.ERROR
        src = _FakeOldElement("shared-mux", recorder)

        @staticmethod
        def parse_error() -> tuple[str, str]:
            return "fatal stream error", "debug"

    engine._loop = _Loop()
    try:
        assert engine._on_bus(None, _Message()) is True
        assert engine._error == ("fatal stream error", "debug")
        assert engine._loop.quit_called is True
    finally:
        release.set()
        for thread in engine._abort_retire_threads:
            thread.join(timeout=5.0)


def test_a_fully_retired_aborted_leg_stops_being_attributed(engine_module) -> None:
    """The record is reaped once the leg is provably gone -- thread finished AND
    disposal reported complete -- so the containment never becomes permanent."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    first_leg = _FakeOldElement("aborted-program", recorder)
    engine._pending_reload = _abort_pending(recorder, [], [first_leg])
    engine._abort_pending_reload("error")
    for thread in engine._abort_retire_threads:
        thread.join(timeout=5.0)
    assert engine._belongs_to_aborted_leg(first_leg) is True, (
        "a leg still retiring was already dropped"
    )

    # A later abort reaps the first leg (finished + fully disposed) and attributes
    # only its own.
    second_leg = _FakeOldElement("next-aborted-program", recorder)
    engine._pending_reload = _abort_pending(recorder, [], [second_leg])
    engine._abort_pending_reload("error")
    for thread in engine._abort_retire_threads:
        thread.join(timeout=5.0)

    assert engine._belongs_to_aborted_leg(second_leg) is True
    assert engine._belongs_to_aborted_leg(first_leg) is False


# --- (6) U16: the rebase reference and the first-buffer observations ------------
#
# U16's blocker was that a live one-sided A/V step could not be DECOMPOSED from
# the logs. The shared rebase reference is printed (``finite switch rebased to
# running time``) but its inputs -- each outgoing pad's own end -- are not, and
# nothing reported what the new leg's first buffer actually became once the
# offset had been applied. These tests pin the always-on diagnostics that make
# the next occurrence decomposable. They assert the diagnostics' WIRING and
# FORMAT; the numeric claim about GStreamer's own ``set_offset`` arithmetic is
# measured on a real packaged pipeline in section (7).


def _u16_pending(
    recorder: _Recorder, *, txn_id: int = 28, switch_at_end_of_current: bool = False
) -> tuple[dict[str, Any], _FakeDiagnosticPad, _FakeDiagnosticPad]:
    """A finite (rebase) commit whose outgoing ends and new-leg pads a test owns."""
    video_segment = _FakeSegment(
        base=1_100_000_000, running_time_for_pts={5_000_000_000: 6_100_000_000}
    )
    audio_segment = _FakeSegment(
        base=1_100_000_000, running_time_for_pts={5_000_000_000: 6_120_000_000}
    )
    new_video_src = _FakeDiagnosticPad(
        "new-video-src", recorder, sticky=_FakeStickyEvent(video_segment)
    )
    new_audio_src = _FakeDiagnosticPad(
        "new-audio-src", recorder, sticky=_FakeStickyEvent(audio_segment)
    )
    old_video_pad = _FakeOldPad("old-video", recorder, peer=None)
    old_audio_pad = _FakeOldPad("old-audio", recorder, peer=None)
    pending: dict[str, Any] = {
        "txn_id": txn_id,
        "timeout_id": None,
        "defer_timeout_id": None,
        "new_video_pad": object(),
        "new_audio_pad": object(),
        "new_elements": [],
        "new_src_pads": [new_video_src, new_audio_src],
        "hold_probes": [(new_video_src, "video-hold"), (new_audio_src, "audio-hold")],
        "old_video_pad": old_video_pad,
        "old_audio_pad": old_audio_pad,
        "old_elements": [],
        "outgoing_end": {
            old_video_pad: {"end": 1_000_000_000, "segment": None},
            old_audio_pad: {"end": 1_100_000_000, "segment": None},
        },
        "rebase_new_leg": True,
        "switch_at_end_of_current": switch_at_end_of_current,
        "old_tail_drop_probes": [],
        "commit_in_progress": True,
    }
    return pending, new_video_src, new_audio_src


def test_u16_rebase_reference_line_names_every_outgoing_end(engine_module) -> None:
    """The line that decomposes the next one-sided step: each pad's OWN end."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, _video, _audio = _u16_pending(recorder)

    line = engine._rebase_reference_diagnostic(
        pending,
        switch_running_time=1_100_000_000,
        rebase_fallback=False,
        pipeline_running_time_ms=None,
    )

    assert line == (
        "CTRL reload diagnostic: rebase-reference reload_id=28 mode=immediate "
        "streams=2 fallback=no ends=[video=1.000,audio=1.100] "
        "pipeline_running_time=none switch_running_time=1.100"
    )


def test_u16_rebase_reference_line_marks_the_fallback_and_a_missing_end(
    engine_module,
) -> None:
    """No observed end (a leg that EOS'd at once) is named, not silently dropped."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, _video, _audio = _u16_pending(recorder, txn_id=4, switch_at_end_of_current=True)
    pending["outgoing_end"] = {
        pad: {"end": None, "segment": None}
        for pad in (pending["old_video_pad"], pending["old_audio_pad"])
    }

    line = engine._rebase_reference_diagnostic(
        pending,
        switch_running_time=1_799_412_000_000,
        rebase_fallback=True,
        pipeline_running_time_ms=1_799_412,
    )

    assert line == (
        "CTRL reload diagnostic: rebase-reference reload_id=4 mode=deferred "
        "streams=2 fallback=yes ends=[video=none,audio=none] "
        "pipeline_running_time=1799.412 switch_running_time=1799.412"
    )


def test_u16_commit_arms_the_new_leg_observation_before_it_releases_the_holds(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """One self-removing first-buffer probe per new-leg stream, armed in the
    window between the offset and the hold release.

    Arming there is what makes the observation race-free: the leg's first buffer
    cannot flow until ``_release_hold_probes`` lifts the block, so every buffer
    the probe can see already carries the rebase offset."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, new_video_src, new_audio_src = _u16_pending(recorder)
    engine._pending_reload = pending
    engine._prepare_reload_handoff(pending)

    engine._begin_reload_commit(pending)

    calls = recorder.calls
    video_offset = _index_of(calls, "set_offset:new-video-src:1100000000")
    audio_offset = _index_of(calls, "set_offset:new-audio-src:1100000000")
    arm_video = _index_of(calls, "add_probe:new-video-src:1:_report_new_leg_first_buffer")
    arm_audio = _index_of(calls, "add_probe:new-audio-src:1:_report_new_leg_first_buffer")
    release_video = _index_of(calls, "remove_probe:new-video-src:video-hold")
    release_audio = _index_of(calls, "remove_probe:new-audio-src:audio-hold")
    assert max(video_offset, audio_offset) < min(arm_video, arm_audio), calls
    assert max(arm_video, arm_audio) < min(release_video, release_audio), calls

    err = capsys.readouterr().err
    assert (
        "CTRL reload diagnostic: rebase-reference reload_id=28 mode=immediate "
        "streams=2 fallback=no ends=[video=1.000,audio=1.100] "
        "pipeline_running_time=none switch_running_time=1.100"
    ) in err

    # Fire each recorded new-leg probe the way the leg's own streaming thread
    # would: that stream's first buffer, after the offset is applied.
    for pad, label, running in (
        (new_video_src, "video", "running_time=6.100"),
        (new_audio_src, "audio", "running_time=6.120"),
    ):
        mask, callback = pad.probes[0]
        assert mask == 1, f"expected Gst.PadProbeType.BUFFER, got {mask}"
        assert callback(pad, _FakeProbeInfo(_FakeProbeBuffer(5_000_000_000))) == (
            engine_module.Gst.PadProbeReturn.REMOVE
        )
        assert (
            f"CTRL reload diagnostic: new-leg-first-buffer stream={label} reload_id=28 "
            f"applied_offset=1.100 pts=5.000 {running} segment_base=1.100"
        ) in capsys.readouterr().err


def test_u16_commit_reports_the_pipeline_time_fallback_when_nothing_was_observed(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fallback reference is named as the fallback, with the pipeline's own
    running time beside it -- that is how a ``5069.451s``-style line becomes
    checkable against the worker's uptime."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.pipeline = _FakeClockPipeline(recorder, clock_time_ns=1_799_412_000_000)
    pending, _video, _audio = _u16_pending(recorder, txn_id=9)
    pending["outgoing_end"] = {}
    engine._pending_reload = pending
    engine._prepare_reload_handoff(pending)

    engine._begin_reload_commit(pending)

    calls = recorder.calls
    assert "set_offset:new-video-src:1799412000000" in calls, calls
    err = capsys.readouterr().err
    assert (
        "CTRL reload diagnostic: rebase-reference reload_id=9 mode=immediate "
        "streams=2 fallback=yes ends=[video=none,audio=none] "
        "pipeline_running_time=1799.412 switch_running_time=1799.412"
    ) in err


def test_u16_new_leg_first_buffer_line_says_none_when_it_cannot_measure(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """A diagnostic must never be able to fail a streaming thread.

    With no sticky SEGMENT and, in the first call, no probe info at all, the
    line still prints (naming what it could not measure) and the probe still
    removes itself. ``pts`` is reported independently of the segment, so a pad
    whose segment is missing is still informative."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    line = engine._new_leg_first_buffer_diagnostic(
        label="video", txn_id=7, applied_offset=5_000_000_000, measured=None
    )
    assert line == (
        "CTRL reload diagnostic: new-leg-first-buffer stream=video reload_id=7 "
        "applied_offset=5.000 pts=none running_time=none segment_base=none"
    )

    pad = _FakeDiagnosticPad("new-video-src", recorder, sticky=None)
    engine._arm_new_leg_rebase_diagnostics({"txn_id": 7, "new_src_pads": [pad]}, 5_000_000_000)

    _mask, callback = pad.probes[0]
    assert callback(pad, None) == engine_module.Gst.PadProbeReturn.REMOVE
    assert callback(pad, _FakeProbeInfo(_FakeProbeBuffer(4_000_000_000))) == (
        engine_module.Gst.PadProbeReturn.REMOVE
    )
    err = capsys.readouterr().err
    assert err.count("CTRL reload diagnostic: new-leg-first-buffer") == 2
    assert (
        "stream=video reload_id=7 applied_offset=5.000 pts=none running_time=none segment_base=none"
    ) in err
    assert (
        "stream=video reload_id=7 applied_offset=5.000 "
        "pts=4.000 running_time=none segment_base=none"
    ) in err
    # The label falls back to the pad's own name when there is no pad identity
    # to compare against, and the mux-side label comes from the negotiated caps.
    assert "stream=video" in err


def test_u16_commit_arms_the_selector_side_observation_in_the_same_window(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reading that shows the offset, armed where the offset is visible.

    ``Gst.Pad.set_offset`` materialises at the CROSSING to the pad's peer, so the
    new-leg tail src pads report that leg's own PRE-offset timeline (measured on
    the packaged 1.28.5 runtime in section (7): the offset pad said
    ``segment_base=0.000 running=0.300`` for the buffer its peer reported as
    ``segment_base=5.000 running=5.300``). The selector sink pads are on the far
    side of that crossing, and their running time is the number that has to line
    up with ``switch_running_time`` for a seam with no step. Same window as the
    tail-pad probe: after the offsets, before the holds lift."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, _new_video_src, _new_audio_src = _u16_pending(recorder)
    receiving = _FakeSegment(
        base=1_100_000_000, running_time_for_pts={5_000_000_000: 6_100_000_000}
    )
    pending["new_video_pad"] = _FakeDiagnosticPad(
        "new-video-selector", recorder, sticky=_FakeStickyEvent(receiving)
    )
    pending["new_audio_pad"] = _FakeDiagnosticPad(
        "new-audio-selector", recorder, sticky=_FakeStickyEvent(receiving)
    )
    engine._pending_reload = pending
    engine._prepare_reload_handoff(pending)

    engine._begin_reload_commit(pending)

    calls = recorder.calls
    arms = [
        _index_of(calls, "add_probe:new-video-selector:1:_report_new_leg_selector_first_buffer"),
        _index_of(calls, "add_probe:new-audio-selector:1:_report_new_leg_selector_first_buffer"),
    ]
    releases = [
        _index_of(calls, "remove_probe:new-video-src:video-hold"),
        _index_of(calls, "remove_probe:new-audio-src:audio-hold"),
    ]
    offsets = [
        _index_of(calls, "set_offset:new-video-src:1100000000"),
        _index_of(calls, "set_offset:new-audio-src:1100000000"),
    ]
    assert max(offsets) < min(arms), calls
    assert max(arms) < min(releases), calls

    for pad, label in (
        (pending["new_video_pad"], "video"),
        (pending["new_audio_pad"], "audio"),
    ):
        mask, callback = pad.probes[0]
        assert mask == 1, f"expected Gst.PadProbeType.BUFFER, got {mask}"
        assert callback(pad, _FakeProbeInfo(_FakeProbeBuffer(5_000_000_000))) == (
            engine_module.Gst.PadProbeReturn.REMOVE
        )
        assert (
            f"CTRL reload diagnostic: new-leg-selector-first-buffer stream={label} "
            f"pad=new-{label}-selector reload_id=28 applied_offset=1.100 pts=5.000 "
            "running_time=6.100 segment_base=1.100"
        ) in capsys.readouterr().err


@pytest.mark.parametrize("absent", [None, object()])
def test_u16_selector_side_observation_is_silent_without_a_probeable_pad(
    engine_module, capsys: pytest.CaptureFixture[str], absent: Any
) -> None:
    """An audio-less leg, or a pad that cannot take a probe: silent, no raise."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    engine._arm_new_leg_selector_diagnostics(
        {"txn_id": 7, "new_video_pad": absent, "new_audio_pad": None}, 5_000_000_000
    )

    assert capsys.readouterr().err == ""
    assert recorder.calls == []


def test_u16_mux_first_buffer_line_labels_each_stream_from_its_caps(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The start-side observation: the first buffer to reach each mux sink pad.

    This is the value that says whether the output side ever saw the offset --
    the piece U16 was missing when it tried to place the live +108.95s step."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    segment = _FakeSegment(base=0, running_time_for_pts={0: 0})
    video_pad = _FakeDiagnosticPad(
        "sink",
        recorder,
        sticky=_FakeStickyEvent(segment),
        caps="video/x-h264, stream-format=byte-stream",
    )
    audio_pad = _FakeDiagnosticPad(
        "sink_1", recorder, sticky=_FakeStickyEvent(segment), caps="audio/mpeg, mpegversion=4"
    )
    engine.mux = _FakeMux([video_pad, audio_pad])

    engine._install_mux_input_diagnostics()

    assert "add_probe:sink:1:_report_mux_first_buffer" in recorder.calls, recorder.calls
    assert "add_probe:sink_1:1:_report_mux_first_buffer" in recorder.calls, recorder.calls
    for pad, label in ((video_pad, "video"), (audio_pad, "audio")):
        _mask, callback = pad.probes[0]
        assert callback(pad, _FakeProbeInfo(_FakeProbeBuffer(0))) == (
            engine_module.Gst.PadProbeReturn.REMOVE
        )
        assert (
            f"CTRL start diagnostic: mux-first-buffer stream={label} pad={pad.name} "
            "pts=0.000 running_time=0.000 segment_base=0.000"
        ) in capsys.readouterr().err


@pytest.mark.parametrize("mux", [None, _FakeMux([]), object()])
def test_u16_mux_start_diagnostic_is_silent_and_safe_without_sink_pads(
    engine_module, capsys: pytest.CaptureFixture[str], mux: Any
) -> None:
    """No mux, no sink pads, or a mux with no iterator: silent, and no raise."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.mux = mux

    engine._install_mux_input_diagnostics()

    assert capsys.readouterr().err == ""
    assert recorder.calls == []


def test_u16_diagnostics_do_not_disturb_the_reload_preroll_log_grader() -> None:
    """The always-on lines must not create, hide, or fake a commit proof.

    ``scripts/ops/check_reload_preroll.py`` grades reload commits from worker
    logs by regex; an unrelated line that happens to contain "rebased" or
    "preroll" would corrupt that verdict."""
    log = "\n".join(
        [
            "CTRL reload: new leg stream held at its first buffer "
            "(0 stream(s) still to preroll) (reload_id=4)",
            "CTRL reload: new leg preroll verified (reload_id=4) held_streams=2 "
            "timing=finite mode=deferred",
            "CTRL reload diagnostic: stage=switching-selector",
            "CTRL reload diagnostic: rebase-reference reload_id=4 mode=deferred streams=2 "
            "fallback=no ends=[video=1799.339,audio=1799.402] "
            "pipeline_running_time=1799.412 switch_running_time=1799.402",
            "CTRL reload: finite switch rebased to running time 1799.402s mode=deferred "
            "streams=2 reload_id=4",
            "CTRL start diagnostic: mux-first-buffer stream=video pad=sink "
            "pts=1.000 running_time=1.000 segment_base=0.000",
            "CTRL reload diagnostic: new-leg-first-buffer stream=video reload_id=4 "
            "applied_offset=1799.402 pts=1.000 running_time=1800.402 segment_base=1799.402",
            "CTRL reload: firing (reload_id=4)",
            "CTRL reload committed (elements=52)",
        ]
    )

    assert check_log(log) == (1, [])


# --- (7) the same diagnostics on a REAL packaged GStreamer pipeline -----------
#
# Sections (1)-(6) prove the diagnostics are wired and formatted correctly; the
# fake pads answer whatever the test tells them to. This section proves the other
# half: that the same code, run against real Gst pads/buffers/segments through the
# product's own packaged runtime, actually PRINTS the lines and can measure a
# buffer's running time. It is gated on the packaged runtime being declared --
# that is a genuine capability boundary (the bundled GI extension is CPython
# 3.12-only), so when it is absent the test SKIPS CLEANLY and says how to enable
# it, rather than silently passing. When it is present it FAILS on any defect.
_U16_DECLARED_ROOT = os.environ.get("CIVICCAST_GSTREAMER_RUNTIME_ROOT")
_U16_REPO_ROOT = Path(__file__).resolve().parents[2]


def _u16_resolve_version_root() -> Path | None:
    """``<version_root>`` (the dir holding ``python.exe`` + ``dependencies``).

    The declared env var may name EITHER the install root (``<install>``) or the
    version root (``<install>/runtime``); both are shipped shapes, so accept both
    and derive by the same relative arithmetic the product uses. Duplicated from
    ``test_hls_sink_captions.py`` rather than imported: ``tests/egress`` has no
    ``__init__.py``, so a cross-module import would not resolve.
    """
    if not _U16_DECLARED_ROOT:
        return None
    declared = Path(_U16_DECLARED_ROOT)
    if (declared / "python.exe").is_file() and (declared / "dependencies").is_dir():
        return declared
    runtime = declared / "runtime"
    if (runtime / "python.exe").is_file() and (runtime / "dependencies").is_dir():
        return runtime
    return None


_U16_VERSION_ROOT = _u16_resolve_version_root()
_U16_GST_ROOT = _U16_VERSION_ROOT / "dependencies" / "gstreamer" if _U16_VERSION_ROOT else None
_U16_PYTHON = _U16_VERSION_ROOT / "python.exe" if _U16_VERSION_ROOT else None
_U16_GST_AVAILABLE = bool(
    _U16_GST_ROOT
    and (_U16_GST_ROOT / "bin").is_dir()
    and (_U16_GST_ROOT / "python").is_dir()
    and (_U16_GST_ROOT / "lib" / "gstreamer-1.0").is_dir()
    and _U16_PYTHON
    and _U16_PYTHON.is_file()
)

_U16_EMITTER = '''# emitted by the packaged CPython 3.12 interpreter
import contextlib, io, os, sys, time

VERSION_ROOT = os.environ["CIVICCAST_GST_VERSION_ROOT"]
GST = os.path.join(VERSION_ROOT, "dependencies", "gstreamer")
GSTBIN = os.path.join(GST, "bin")
os.environ["GI_TYPELIB_PATH"] = os.path.join(GST, "lib", "girepository-1.0")
os.environ["GST_PLUGIN_PATH"] = os.path.join(GST, "lib", "gstreamer-1.0")
os.environ["PYGI_DLL_DIRS"] = GSTBIN
os.environ["PATH"] = GSTBIN + os.pathsep + os.environ.get("PATH", "")
sys.path.insert(0, os.path.join(GST, "python"))
sys.path.insert(0, os.environ["CIVICCAST_REPO_ROOT"])
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)
from civiccast.egress.gst.engine import GstPlayoutEngine

OFFSET_NS = 5 * Gst.SECOND


def _build():
    """One real pipeline: videotestsrc + audiotestsrc -> mpegtsmux -> fakesink."""
    pipe = Gst.Pipeline.new("u16-real")
    vsrc = Gst.ElementFactory.make("videotestsrc")
    vsrc.set_property("is-live", False)
    vsrc.set_property("num-buffers", 240)
    vcap = Gst.ElementFactory.make("capsfilter")
    vcap.set_property(
        "caps",
        Gst.Caps.from_string("video/x-raw,format=I420,width=320,height=180,framerate=30/1"),
    )
    venc = Gst.ElementFactory.make("openh264enc")
    vparse = Gst.ElementFactory.make("h264parse")
    asrc = Gst.ElementFactory.make("audiotestsrc")
    asrc.set_property("is-live", False)
    asrc.set_property("num-buffers", 480)
    acap = Gst.ElementFactory.make("capsfilter")
    acap.set_property(
        "caps", Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=48000,channels=2")
    )
    aconv = Gst.ElementFactory.make("audioconvert")
    ares = Gst.ElementFactory.make("audioresample")
    aenc = Gst.ElementFactory.make("voaacenc")
    apar = Gst.ElementFactory.make("aacparse")
    mux = Gst.ElementFactory.make("mpegtsmux")
    sink = Gst.ElementFactory.make("fakesink")
    sink.set_property("sync", False)
    elements = [vsrc, vcap, venc, vparse, asrc, acap, aconv, ares, aenc, apar, mux, sink]
    links = [
        (vsrc, vcap), (vcap, venc), (venc, vparse), (vparse, mux),
        (asrc, acap), (acap, aconv), (aconv, ares), (ares, aenc), (aenc, apar),
        (apar, mux), (mux, sink),
    ]
    for element in elements:
        pipe.add(element)
    for upstream, downstream in links:
        assert upstream.link(downstream), (upstream.get_name(), downstream.get_name())
    return pipe, mux, vparse, apar


def _run(pipe, arm):
    """PLAY the pipeline with ``arm()`` run first; return everything printed to
    PYTHON's stderr (which is where the diagnostics write)."""
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        arm()
        pipe.set_state(Gst.State.PLAYING)
        bus = pipe.get_bus()
        deadline = time.time() + 45
        while time.time() < deadline:
            message = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message:
                if message.type == Gst.MessageType.ERROR:
                    print("GST_ERROR", message.parse_error(), file=sys.stderr)
                    sys.exit(3)
                break
            time.sleep(0.05)
        pipe.set_state(Gst.State.NULL)
    return captured.getvalue()


# 1. Start side: one line per stream, from the first buffer that reaches each
#    mux SINK pad. Installed after linking (so the request pads exist) and before
#    PLAYING (so the FIRST buffer -- not a later one -- fires it), exactly the
#    window run_forever uses.
pipe, mux, vparse, _apar = _build()
mux_stub = object.__new__(GstPlayoutEngine)
mux_stub.mux = mux
text = _run(pipe, lambda: GstPlayoutEngine._install_mux_input_diagnostics(mux_stub))
start_lines = [ln for ln in text.splitlines() if ln.startswith("CTRL start diagnostic:")]
assert len(start_lines) == 2, (start_lines, text)
assert any("stream=video" in ln for ln in start_lines), start_lines
assert any("stream=audio" in ln for ln in start_lines), start_lines
for line in start_lines:
    assert "pts=none" not in line, line
    assert "running_time=none" not in line, line
    print("U16_LINE " + line)

# 2. Reload side: the new leg's first buffer AFTER the offset. The offset is set
#    before PLAYING and the probe armed behind it, which is the production
#    order (the leg is held at that buffer when ``set_offset`` runs).
pipe2, _mux2, vparse2, _apar2 = _build()
leg_stub = object.__new__(GstPlayoutEngine)


def _arm_new_leg():
    pad = vparse2.get_static_pad("src")
    pad.set_offset(OFFSET_NS)
    GstPlayoutEngine._arm_new_leg_rebase_diagnostics(
        leg_stub, {"txn_id": 7, "new_src_pads": [pad]}, OFFSET_NS
    )


text2 = _run(pipe2, _arm_new_leg)
leg_lines = [ln for ln in text2.splitlines() if ln.startswith("CTRL reload diagnostic:")]
assert len(leg_lines) == 1, (leg_lines, text2)
line = leg_lines[0]
assert "new-leg-first-buffer stream=video reload_id=7 applied_offset=5.000" in line, line
assert "pts=none" not in line, line
assert "running_time=none" not in line, line
print("U16_LINE " + line)

# 3. The same first buffer, read on the RECEIVING side of the offset crossing.
#    The offset is set on the video leg's tail src pad; its PEER (the mux request
#    sink pad) is where the shift becomes visible. The audio leg is left
#    un-offset as a control inside the same pipeline, so exactly one of the two
#    lines must carry the offset -- and it must be the video one.
pipe3, _mux3, vparse3, apar3 = _build()
video_peer = vparse3.get_static_pad("src").get_peer()
audio_peer = apar3.get_static_pad("src").get_peer()
assert video_peer is not None and audio_peer is not None, "the offset pad has no peer"
sel_stub = object.__new__(GstPlayoutEngine)


def _arm_selector_side():
    vparse3.get_static_pad("src").set_offset(OFFSET_NS)
    GstPlayoutEngine._arm_new_leg_selector_diagnostics(
        sel_stub,
        {"txn_id": 9, "new_video_pad": video_peer, "new_audio_pad": audio_peer},
        OFFSET_NS,
    )


text3 = _run(pipe3, _arm_selector_side)
sel_lines = [
    ln
    for ln in text3.splitlines()
    if ln.startswith("CTRL reload diagnostic: new-leg-selector-first-buffer")
]
assert len(sel_lines) == 2, (sel_lines, text3)
shifted = [ln for ln in sel_lines if "segment_base=5.000" in ln]
assert len(shifted) == 1, sel_lines
line3 = shifted[0]
assert "stream=video" in line3, sel_lines
video_running = line3.split("running_time=")[1].split()[0]
assert video_running != "none" and float(video_running) >= 5.0, line3
control = [ln for ln in sel_lines if ln is not line3][0]
control_running = control.split("running_time=")[1].split()[0]
assert "segment_base=0.000" in control, control
assert control_running != "none" and float(control_running) < 5.0, control
print("U16_LINE " + line3)
print("U16_LINE " + control)
print("U16_EMIT_OK")
'''


@pytest.mark.skipif(
    not _U16_GST_AVAILABLE,
    reason=(
        "packaged GStreamer runtime not declared, so the real-pipeline half of "
        "the U16 diagnostics is NOT verified here -- set "
        "CIVICCAST_GSTREAMER_RUNTIME_ROOT to the install root (e.g. "
        "C:\\Program Files\\CivicCast (Native)) to run it against the bundled runtime"
    ),
)
def test_u16_diagnostics_print_on_a_real_packaged_pipeline(tmp_path: Path) -> None:
    """The real-GStreamer half: the diagnostics print, and can measure, for real."""
    assert _U16_VERSION_ROOT is not None and _U16_PYTHON is not None
    runner = tmp_path / "u16_emitter.py"
    runner.write_text(_U16_EMITTER, encoding="utf-8")
    env = dict(os.environ)
    env["CIVICCAST_REPO_ROOT"] = str(_U16_REPO_ROOT)
    # The child imports the engine, and the engine's import-time bootstrap reads
    # CIVICCAST_GSTREAMER_RUNTIME_ROOT itself -- and that var must name the
    # VERSION root (``<install>/runtime``), the directory that also holds
    # ``python.exe``, because ``dependencies/gstreamer`` only resolves under it
    # (civiccast/native/gstreamer_runtime.py's module note). The user may have
    # declared the install root, so pass the resolved version root explicitly
    # instead of forwarding whatever shape was declared.
    env["CIVICCAST_GST_VERSION_ROOT"] = str(_U16_VERSION_ROOT)
    env["CIVICCAST_GSTREAMER_RUNTIME_ROOT"] = str(_U16_VERSION_ROOT)
    # An inherited PYTHONPATH would shadow the bundled ``gi``; drop it so the
    # child imports the packaged bindings (same discipline as the captions test).
    env.pop("PYTHONPATH", None)
    emit = subprocess.run(
        [str(_U16_PYTHON), str(runner)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert emit.returncode == 0, f"U16 real-runtime emit failed:\n{emit.stdout}\n{emit.stderr}"
    assert "U16_EMIT_OK" in emit.stdout, f"emit produced no marker:\n{emit.stdout}\n{emit.stderr}"
    assert "U16_LINE CTRL start diagnostic: mux-first-buffer stream=video " in emit.stdout, (
        emit.stdout
    )
    assert "U16_LINE CTRL start diagnostic: mux-first-buffer stream=audio " in emit.stdout, (
        emit.stdout
    )
    assert (
        "U16_LINE CTRL reload diagnostic: new-leg-first-buffer stream=video reload_id=7 "
        "applied_offset=5.000" in emit.stdout
    ), emit.stdout
    selector_lines = [
        line
        for line in emit.stdout.splitlines()
        if line.startswith("U16_LINE CTRL reload diagnostic: new-leg-selector-first-buffer")
    ]
    assert len(selector_lines) == 2, emit.stdout
    assert any(
        "stream=video" in line and "segment_base=5.000" in line for line in selector_lines
    ), emit.stdout


def test_u16_packaged_runtime_resolution_matches_its_declared_root() -> None:
    """When the env var IS declared, the gate must resolve to available.

    A silently-wrong resolver would turn every real-runtime test into a skip --
    "not verified" disguised as "nothing to verify".
    """
    if not _U16_DECLARED_ROOT:
        pytest.skip(
            "CIVICCAST_GSTREAMER_RUNTIME_ROOT not declared, so the real-runtime "
            "half of these diagnostics is unverified on this host"
        )
    assert _U16_GST_AVAILABLE, (
        f"declared {_U16_DECLARED_ROOT!r} did not resolve to a packaged runtime "
        f"(version root {_U16_VERSION_ROOT!r})"
    )


# --- (8) U30: naming WHICH stream stopped feeding the mux ---------------------
#
# Both live incidents of 2026-09-25 (education 06:27, government 01:03) committed
# a deferred program->program reload at a segment boundary and then went silent:
# the HLS window froze, the relay self-heal rewrote nothing, and only a full
# channel restart recovered. Read off the education worker's own stderr, the
# TOTAL mux-src counter kept ADVANCING right through the freeze -- its
# per-interval deltas were 332-496 before the commit and 234/281 after it, i.e.
# the audio-only rate. So the channel was airing audio with no video at all, and
# nothing in the log said so (the S9-5 watchdog compares that same total, so it
# could not fire either).
#
# Two always-on, read-only diagnostics close that gap. Neither changes behaviour:
# every body is exception-guarded, and every probe only counts or prints.


class _FakeEventProbeInfo:
    """Probe info carrying an EVENT -- the U16 fakes only ever carried a buffer."""

    def __init__(self, event: Any) -> None:
        self.type = _FakePadProbeType.EVENT_DOWNSTREAM
        self._event = event

    def get_buffer(self) -> Any:
        return None

    def get_event(self) -> Any:
        return self._event


class _FakeProbeEvent:
    def __init__(self, event_type: Any) -> None:
        self.type = event_type


def _u30_pending_for_eos(pad: Any, *, txn_id: int = 9) -> dict[str, Any]:
    """The subset of a pending reload that ``_on_old_leg_eos`` actually reads."""
    return {
        "txn_id": txn_id,
        "outgoing_end": {},
        "boundary_probes": [(pad, 1)],
        "outgoing_eos_pads": set(),
        "old_video_pad": pad,
        "new_leg_ready": False,
    }


def test_u30_mux_input_counters_count_each_sink_pad_separately(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """One counter per mux SINK pad, so a starved stream is a first-class signal.

    The mux SRC counter can only ever say "output is still advancing"; a channel
    whose video branch stopped feeding the mux while audio continues keeps that
    counter advancing at exactly the audio-only rate, which is the shape both
    live incidents had. Counting at the SINK pads is what makes video's own
    silence visible."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video_pad = _FakeDiagnosticPad(
        "sink_65", recorder, caps="video/x-h264, stream-format=byte-stream"
    )
    audio_pad = _FakeDiagnosticPad("sink_66", recorder, caps="audio/mpeg, mpegversion=4")
    engine.mux = _FakeMux([video_pad, audio_pad])

    engine._install_mux_input_counters()

    assert "add_probe:sink_65:1:_count_mux_input" in recorder.calls, recorder.calls
    assert "add_probe:sink_66:1:_count_mux_input" in recorder.calls, recorder.calls
    assert engine._mux_input_buffers == {"sink_65": 0, "sink_66": 0}
    assert capsys.readouterr().err == ""

    for _ in range(4):
        _mask, callback = video_pad.probes[0]
        assert callback(video_pad, _FakeProbeInfo(_FakeProbeBuffer(0))) == (
            engine_module.Gst.PadProbeReturn.OK
        )
    _mask, callback = audio_pad.probes[0]
    callback(audio_pad, _FakeProbeInfo(_FakeProbeBuffer(0)))
    assert engine._mux_input_buffers == {"sink_65": 4, "sink_66": 1}


@pytest.mark.parametrize("mux", [None, _FakeMux([]), object()])
def test_u30_mux_input_counters_are_silent_and_safe_without_sink_pads(
    engine_module, capsys: pytest.CaptureFixture[str], mux: Any
) -> None:
    """No mux, no sink pads, or a mux with no iterator: silent, and no raise."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.mux = mux

    engine._install_mux_input_counters()

    assert capsys.readouterr().err == ""
    assert recorder.calls == []
    assert engine._mux_input_buffers == {}


def test_u30_mux_input_counters_skip_a_pad_that_cannot_take_a_probe(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pad that cannot be probed must NOT be listed.

    A stream that is listed and then reads ``+0`` forever would be read as "that
    stream stopped flowing", which would be a lie: the truth is "we could not
    count this stream at all", and the right rendering of that is absence."""
    recorder = _Recorder()

    class _RefusingPad(_FakeDiagnosticPad):
        def add_probe(self, mask: Any, callback: Any) -> str:
            raise RuntimeError("probe refused")

    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.mux = _FakeMux(
        [
            _RefusingPad("sink_65", recorder, caps="video/x-h264"),
            _FakeDiagnosticPad("sink_66", recorder, caps="audio/mpeg, mpegversion=4"),
        ]
    )

    engine._install_mux_input_counters()

    assert engine._mux_input_buffers == {"sink_66": 0}
    assert capsys.readouterr().err == ""


def test_u30_mux_input_deltas_report_each_stream_and_advance_the_baseline(
    engine_module,
) -> None:
    """Per-interval deltas, not a running total: ``video=+0`` is the signal.

    The baseline is keyed by PAD NAME, not by resolved label -- the label comes
    from negotiated caps, and a snapshot taken before negotiation would key on
    something the next print cannot find, which would read as ``+0`` (a lie in
    the exact direction this diagnostic exists to detect)."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_pads = {
        "sink_65": _FakeDiagnosticPad("sink_65", _Recorder(), caps="video/x-h264"),
        "sink_66": _FakeDiagnosticPad("sink_66", _Recorder(), caps="audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 100, "sink_66": 200}
    engine._mux_input_snapshot = {"sink_65": 90, "sink_66": 190}
    engine._mux_input_snapshot_t = 10.0

    assert engine._mux_input_delta_suffix(5.0) == " [mux-in 5.0s: video=+10 audio=+10]"

    # A print advances the baseline to the counters it just reported, so the NEXT
    # line reports its own interval only.
    engine._snapshot_mux_input(15.0)
    assert engine._mux_input_snapshot == {"sink_65": 100, "sink_66": 200}
    assert engine._mux_input_snapshot_t == 15.0

    engine._mux_input_buffers = {"sink_65": 150, "sink_66": 400}
    assert engine._mux_input_delta_suffix(5.0) == " [mux-in 5.0s: video=+50 audio=+200]"


def test_u30_mux_input_deltas_show_a_stream_that_stopped_flowing(engine_module) -> None:
    """The incident shape in one log line: video flat, audio still moving.

    The numbers are the ones MEASURED off the live education worker: 234 buffers
    per 5s is that channel's audio-only rate (461 over 6s), against 332-496 per
    interval for healthy A/V."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_pads = {
        "sink_65": _FakeDiagnosticPad("sink_65", _Recorder(), caps="video/x-h264"),
        "sink_66": _FakeDiagnosticPad("sink_66", _Recorder(), caps="audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145750}
    engine._mux_input_snapshot = {"sink_65": 138577, "sink_66": 145516}

    assert engine._mux_input_delta_suffix(5.0) == " [mux-in 5.0s: video=+0 audio=+234]"


def test_u30_mux_input_deltas_put_video_and_audio_first_then_others(engine_module) -> None:
    """Video first, then audio, then anything else by name.

    An unrecognised pad label (an unnegotiated pad falls back to its own name)
    must still be reported rather than dropped -- the point of the line is that
    nothing which feeds the mux is invisible."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_pads = {
        "sink_67": _FakeDiagnosticPad("sink_67", _Recorder()),
        "sink_66": _FakeDiagnosticPad("sink_66", _Recorder(), caps="audio/mpeg, mpegversion=4"),
        "sink_65": _FakeDiagnosticPad("sink_65", _Recorder(), caps="video/x-h264"),
    }
    engine._mux_input_buffers = {"sink_65": 1, "sink_66": 2, "sink_67": 3}
    engine._mux_input_snapshot = {"sink_65": 0, "sink_66": 0, "sink_67": 0}

    assert engine._mux_input_delta_suffix(5.0) == (" [mux-in 5.0s: video=+1 audio=+2 sink_67=+3]")


def test_u30_mux_input_deltas_are_empty_when_nothing_was_counted(engine_module) -> None:
    """No counted pad: no suffix at all, so the existing line is byte-identical."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_buffers = {}
    engine._mux_input_pads = {}
    engine._mux_input_snapshot = {}

    assert engine._mux_input_delta_suffix(5.0) == ""


def test_u30_a_dropped_outgoing_eos_is_named_before_it_is_dropped(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The line that tells "the outgoing leg never ended" from "it ended and we
    dropped the event".

    Until U30 the only trace of this path was the SETTLEMENT line the queued
    callback prints later, so an EOS that was dropped and then declined by one of
    ``_on_old_leg_eos``'s four guards left no line at all. The education incident
    recorded ``stream=audio (1/2 stream(s))`` and never settled video; the U30
    off-live reproduction has runs where the worker exits CLEANLY with no settle
    line and no commit stage ever printed."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pad = _FakeDiagnosticPad("sink_65", recorder)
    engine._pending_reload = _u30_pending_for_eos(pad)

    result = engine._on_outgoing_pad_data(
        pad, _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)), 9
    )

    assert result == engine_module.Gst.PadProbeReturn.DROP
    err = capsys.readouterr().err
    assert (
        "CTRL reload diagnostic: outgoing-EOS-dropped pad=sink_65 stream=video pending_txn=9"
    ) in err
    # The settle line that already existed still follows it (the fixture's fake
    # ``GLib.idle_add`` runs the queued callback inline).
    assert "CTRL reload: outgoing EOS observed stream=video (1/1 stream(s))" in err
    assert engine._pending_reload["old_leg_eos"] is True


def test_u30_a_dropped_eos_from_a_superseded_transaction_says_so(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dropped with NO settle line is the state that used to be invisible.

    A stale queued EOS from a superseded transaction is dropped here and then
    declined by the transaction-id guard in ``_on_old_leg_eos``. Before U30 the
    log showed neither event; now the drop is named, and the absence of the
    settle line after it is the distinction."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pad = _FakeDiagnosticPad("sink_65", recorder)
    engine._pending_reload = _u30_pending_for_eos(pad, txn_id=9)

    result = engine._on_outgoing_pad_data(
        pad, _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)), 8
    )

    assert result == engine_module.Gst.PadProbeReturn.DROP
    err = capsys.readouterr().err
    assert (
        "CTRL reload diagnostic: outgoing-EOS-dropped pad=sink_65 stream=video pending_txn=9"
    ) in err
    assert "outgoing EOS observed" not in err
    assert not engine._pending_reload.get("old_leg_eos")


def test_u30_a_dropped_eos_with_no_pending_transaction_is_still_named(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The retired-leg window: reload already committed, leg not yet disposed.

    The probe deliberately stays installed and drops EOS unconditionally; this is
    the line that shows an EOS arrived in that window even though there is no
    transaction left to settle."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pad = _FakeDiagnosticPad("sink_66", recorder)
    engine._pending_reload = None

    result = engine._on_outgoing_pad_data(
        pad, _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)), 9
    )

    assert result == engine_module.Gst.PadProbeReturn.DROP
    assert (
        "CTRL reload diagnostic: outgoing-EOS-dropped pad=sink_66 "
        "stream=<not-a-leg-pad> pending_txn=none" in capsys.readouterr().err
    )


def test_u30_diagnostics_do_not_disturb_the_reload_preroll_log_grader() -> None:
    """U16's guard, applied to the U30 lines.

    ``scripts/ops/check_reload_preroll.py`` grades reload commits from worker
    logs by regex; an unrelated line containing "preroll"/"rebased" in the wrong
    shape would corrupt that verdict. The new lines must neither create, hide,
    nor fake a commit proof."""
    log = "\n".join(
        [
            "CTRL reload: new leg stream held at its first buffer "
            "(0 stream(s) still to preroll) (reload_id=4)",
            "CTRL reload: new leg preroll verified (reload_id=4) held_streams=2 "
            "timing=finite mode=deferred",
            "CTRL reload diagnostic: outgoing-EOS-dropped pad=sink_65 pending_txn=4",
            "CTRL output: 138577 buffers (+138576) since PLAYING "
            "[mux-in 5.0s: video=+0 audio=+234]",
            "CTRL reload: finite switch rebased to running time 1799.402s mode=deferred "
            "streams=2 reload_id=4",
            "CTRL reload: firing (reload_id=4)",
            "CTRL reload committed (elements=52)",
        ]
    )

    assert check_log(log) == (1, [])


class _FakeChainElement:
    """A selector / isolation queue as the flow-ladder arming sees it.

    ``get_static_pad`` is the only method the arming path calls; it returns the
    src pad by name and ``None`` for anything else, exactly like
    ``Gst.Element.get_static_pad`` on a pad that does not exist.

    ``get_property`` answers ``active-pad`` with whatever the test set (``None``
    by default) -- the read the ``sel`` EOS label makes lazily, at EOS time."""

    def __init__(
        self,
        name: str,
        recorder: _Recorder,
        src_pad: Any = None,
        *,
        active_pad: Any = None,
    ) -> None:
        self.name = name
        self.recorder = recorder
        self.src_pad = src_pad
        self.active_pad = active_pad

    def get_static_pad(self, pad_name: str) -> Any:
        self.recorder.calls.append(f"get_static_pad:{self.name}:{pad_name}")
        return self.src_pad if pad_name == "src" else None

    def get_property(self, prop_name: str) -> Any:
        self.recorder.calls.append(f"get_property:{self.name}:{prop_name}")
        return self.active_pad


class _FakeNamedPipeline(_FakePipeline):
    """A pipeline that can also look an element up by name (the isolation
    queues are built as locals in ``_build``, so the ladder finds them the way
    any other code would: by their stable element names)."""

    def __init__(self, recorder: _Recorder, elements: dict[str, Any]) -> None:
        super().__init__(recorder)
        self._elements = elements

    def get_by_name(self, name: str) -> Any:
        self.recorder.calls.append(f"pipeline.get_by_name:{name}")
        return self._elements.get(name)


_VIDEO_QUEUE_NAME = "program_video_selector_isolation"
_AUDIO_QUEUE_NAME = "program_audio_selector_isolation"


def _u30_armed_chain(engine: Any, recorder: _Recorder) -> dict[str, Any]:
    """Install the ladder on a bare engine with a full, countable chain.

    Returns the four src pads keyed by ``(rung, stream)`` so a test can fire
    them the way a streaming thread would."""
    pads = {
        ("sel", "video"): _FakeDiagnosticPad("sel_src", recorder),
        ("sel", "audio"): _FakeDiagnosticPad("asel_src", recorder),
        ("queue", "video"): _FakeDiagnosticPad("vq_src", recorder),
        ("queue", "audio"): _FakeDiagnosticPad("aq_src", recorder),
    }
    engine.selector = _FakeChainElement("sel", recorder, pads[("sel", "video")])
    engine.audio_selector = _FakeChainElement("asel", recorder, pads[("sel", "audio")])
    engine.pipeline = _FakeNamedPipeline(
        recorder,
        {
            _VIDEO_QUEUE_NAME: _FakeChainElement("vq", recorder, pads[("queue", "video")]),
            _AUDIO_QUEUE_NAME: _FakeChainElement("aq", recorder, pads[("queue", "audio")]),
        },
    )
    engine._install_chain_input_counters()
    return pads


def test_u30_chain_input_counters_arm_both_rungs_for_both_streams(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """One counter at each end of the stretch the mux counters cannot see.

    ``[mux-in ...]`` says the output half is still being fed; it says nothing
    about WHERE inside ``selector -> isolation queue -> encoder -> mux`` a
    stream stopped. The dead immediate-switch runs of the 2026-09-25 off-live
    campaign show the switched-in leg's buffer arriving on the selector's own
    input pad, the active-pad readback confirming the switch, and the mux input
    counters never moving for that leg -- so the loss is inside that stretch,
    and the ladder bisects it with one counter at each end."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = _u30_armed_chain(engine, recorder)

    assert sorted(engine._chain_input_pads) == [
        ("queue", "audio"),
        ("queue", "video"),
        ("sel", "audio"),
        ("sel", "video"),
    ]
    assert engine._chain_input_buffers == dict.fromkeys(engine._chain_input_pads, 0)
    assert "get_static_pad:sel:src" in recorder.calls
    assert f"pipeline.get_by_name:{_VIDEO_QUEUE_NAME}" in recorder.calls
    for pad in pads.values():
        assert f"add_probe:{pad.name}:1:_count_chain_input" in recorder.calls, recorder.calls
    assert capsys.readouterr().err == ""

    # Each rung counts ITS OWN stream only -- a selector src pad carries both
    # streams' data over the worker's lifetime, so a shared counter would make
    # video's silence invisible again, which is the whole defect.
    _mask, callback = pads[("sel", "video")].probes[0]
    for _ in range(3):
        assert (
            callback(pads[("sel", "video")], _FakeProbeInfo(_FakeProbeBuffer(0)))
            == engine_module.Gst.PadProbeReturn.OK
        )
    assert engine._chain_input_buffers[("sel", "video")] == 3
    assert engine._chain_input_buffers[("sel", "audio")] == 0
    assert engine._chain_input_buffers[("queue", "video")] == 0


@pytest.mark.parametrize("missing", ["selector", "audio_selector", "video_queue"])
def test_u30_chain_input_counters_skip_a_rung_that_cannot_be_counted(
    engine_module, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    """An uncountable rung must be ABSENT from the ladder, never rendered ``+0``.

    ``+0`` on the line means "this stream sent nothing", which is the exact
    claim the diagnostic exists to make. A rung we could not count must not be
    able to make that claim on a healthy channel, so an element that cannot be
    found (or has no src pad) is left out of the map instead -- and the OTHER
    rungs stay armed, so one unfindable element does not silence the ladder."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = _u30_armed_chain(engine, recorder)
    engine._chain_input_pads = {}
    engine._chain_input_buffers = {}
    if missing == "selector":
        engine.selector = object()  # no get_static_pad at all
    elif missing == "audio_selector":
        engine.audio_selector = None
    else:
        # The video isolation queue cannot be found by name; the audio one can.
        engine.pipeline._elements.pop(_VIDEO_QUEUE_NAME)

    engine._install_chain_input_counters()

    dropped = ("sel", "video") if missing == "selector" else ("sel", "audio")
    if missing == "video_queue":
        dropped = ("queue", "video")
    assert dropped not in engine._chain_input_pads
    assert dropped not in engine._chain_input_buffers
    assert len(engine._chain_input_pads) == 3
    assert pads  # the armable rungs above were still armed
    assert capsys.readouterr().err == ""


def test_u30_chain_input_counters_skip_a_pad_that_refuses_a_probe(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same rule for a pad that raises: absent, not ``+0``."""
    recorder = _Recorder()

    class _RefusingPad(_FakeDiagnosticPad):
        def add_probe(self, mask: Any, callback: Any) -> str:
            raise RuntimeError("probe refused")

    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.selector = _FakeChainElement("sel", recorder, _RefusingPad("sel_src", recorder))
    engine.audio_selector = _FakeChainElement(
        "asel", recorder, _FakeDiagnosticPad("asel_src", recorder)
    )
    engine.pipeline = _FakeNamedPipeline(recorder, {})

    engine._install_chain_input_counters()

    assert sorted(engine._chain_input_pads) == [("sel", "audio")]
    assert engine._chain_input_buffers == {("sel", "audio"): 0}
    assert capsys.readouterr().err == ""


def test_u30_chain_input_deltas_render_upstream_to_downstream(engine_module) -> None:
    """The ladder's shape: rungs in data order, each stream labelled.

    Keyed by ``(rung, stream)`` rather than by pad name, because an
    ``input-selector``'s src pad can be armed before its caps are negotiated --
    and a label read from unnegotiated caps would be the pad name, which the
    next print could not match (reading as ``+0`` forever)."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._chain_input_pads = {
        ("queue", "audio"): _FakeDiagnosticPad("aq_src", _Recorder()),
        ("sel", "video"): _FakeDiagnosticPad("sel_src", _Recorder()),
        ("queue", "video"): _FakeDiagnosticPad("vq_src", _Recorder()),
        ("sel", "audio"): _FakeDiagnosticPad("asel_src", _Recorder()),
    }
    engine._chain_input_buffers = {
        ("sel", "video"): 121,
        ("sel", "audio"): 305,
        ("queue", "video"): 121,
        ("queue", "audio"): 305,
    }
    engine._chain_input_snapshot = {
        ("sel", "video"): 0,
        ("sel", "audio"): 71,
        ("queue", "video"): 0,
        ("queue", "audio"): 71,
    }

    assert engine._chain_input_delta_suffix(5.0) == (
        " [chain-in 5.0s: sel video=+121 audio=+234 | queue video=+121 audio=+234]"
    )


def test_u30_chain_input_deltas_bisect_a_stream_that_never_reached_the_encoder(
    engine_module,
) -> None:
    """The bisection this diagnostic exists for, in one line.

    MEASURED off the dead immediate-switch runs (off-live campaign 5, 2026-09-25):
    the mux input counters read ``video=+121 audio=+186`` over the whole 4.1 s
    run while the switched-in leg's own buffer had already arrived on the
    selector's input pad. If the selector's src rung advances and the queue's
    does not, the data died between the selector and the encode chain; if both
    advance, it died inside the encode chain. Either way the log names the
    stage instead of only the mux."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._chain_input_pads = {
        ("sel", "video"): _FakeDiagnosticPad("sel_src", _Recorder()),
        ("sel", "audio"): _FakeDiagnosticPad("asel_src", _Recorder()),
        ("queue", "video"): _FakeDiagnosticPad("vq_src", _Recorder()),
        ("queue", "audio"): _FakeDiagnosticPad("aq_src", _Recorder()),
    }
    # Selector forwarded nothing more; the queue saw nothing new either.
    engine._chain_input_buffers = {
        ("sel", "video"): 121,
        ("sel", "audio"): 186,
        ("queue", "video"): 53,
        ("queue", "audio"): 53,
    }
    engine._chain_input_snapshot = {
        ("sel", "video"): 121,
        ("sel", "audio"): 0,
        ("queue", "video"): 53,
        ("queue", "audio"): 0,
    }

    assert engine._chain_input_delta_suffix(4.1) == (
        " [chain-in 4.1s: sel video=+0 audio=+186 | queue video=+0 audio=+53]"
    )


def test_u30_the_flow_ladder_rides_the_mux_clause_without_changing_it(
    engine_module,
) -> None:
    """Both clauses on one line, mux clause byte-identical to before.

    The progress line and the pipeline-EOS line already print
    ``_mux_input_delta_suffix``; the ladder is appended to that same string so
    the one line the live incidents already had becomes the whole picture. With
    no ladder armed the string is exactly what it was."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_pads = {
        "sink_65": _FakeDiagnosticPad("sink_65", _Recorder(), caps="video/x-h264"),
        "sink_66": _FakeDiagnosticPad("sink_66", _Recorder(), caps="audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145750}
    engine._mux_input_snapshot = {"sink_65": 138577, "sink_66": 145516}
    engine._chain_input_pads = {}
    engine._chain_input_buffers = {}
    engine._chain_input_snapshot = {}

    assert engine._mux_input_delta_suffix(5.0) == " [mux-in 5.0s: video=+0 audio=+234]"

    engine._chain_input_pads = {
        ("sel", "video"): _FakeDiagnosticPad("sel_src", _Recorder()),
        ("sel", "audio"): _FakeDiagnosticPad("asel_src", _Recorder()),
    }
    engine._chain_input_buffers = {("sel", "video"): 138577, ("sel", "audio"): 145750}
    engine._chain_input_snapshot = {("sel", "video"): 138577, ("sel", "audio"): 145516}

    assert engine._mux_input_delta_suffix(5.0) == (
        " [mux-in 5.0s: video=+0 audio=+234] [chain-in 5.0s: sel video=+0 audio=+234]"
    )


def test_u30_the_flow_ladder_alone_still_renders_with_no_mux_counters(
    engine_module,
) -> None:
    """A graph with a countable chain but no countable mux: the ladder shows.

    Both clauses are independent; the earlier "return ``''`` when nothing was
    counted" contract is about what was COUNTED, not about which half of the
    output graph it was."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_pads = {}
    engine._mux_input_buffers = {}
    engine._mux_input_snapshot = {}
    engine._chain_input_pads = {("sel", "video"): _FakeDiagnosticPad("sel_src", _Recorder())}
    engine._chain_input_buffers = {("sel", "video"): 7}
    engine._chain_input_snapshot = {("sel", "video"): 2}

    assert engine._mux_input_delta_suffix(5.0) == " [chain-in 5.0s: sel video=+5]"


def test_u30_the_flow_ladder_is_empty_when_nothing_was_counted(engine_module) -> None:
    """Neither counter group armed: no suffix at all, line byte-identical."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_pads = {}
    engine._mux_input_buffers = {}
    engine._mux_input_snapshot = {}
    engine._chain_input_pads = {}
    engine._chain_input_buffers = {}
    engine._chain_input_snapshot = {}

    assert engine._mux_input_delta_suffix(5.0) == ""


def test_u30_a_pipeline_eos_names_itself_before_it_quits_the_worker(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The line that tells "the output ENDED" from "the output stopped".

    A bus EOS quits the run loop, so the worker exits and the daemon sees a
    clean teardown -- with no line anywhere saying the channel's output had
    ended. The U30 off-live reproduction has runs in which an immediate reload
    committed and then the mux reached the OUTGOING leg's own end 2.2 s later:
    the tail file stops growing at exactly that PTS and the worker leaves with
    ``{'error': None, 'teardown_clean': True}``. In a log with no EOS line that
    is indistinguishable from an operator's ``stop``."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _QuitCounter:
        def __init__(self) -> None:
            self.quits = 0

        def quit(self) -> None:
            self.quits += 1

    class _EosMessage:
        type = engine_module.Gst.MessageType.EOS
        src = _FakeDiagnosticPad("mpegtsmux_3", recorder)

    loop = _QuitCounter()
    engine._loop = loop
    engine._mux_input_pads = {
        "sink_65": _FakeDiagnosticPad("sink_65", recorder, caps="video/x-h264"),
        "sink_66": _FakeDiagnosticPad("sink_66", recorder, caps="audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 40, "sink_66": 274}
    engine._mux_input_snapshot = {"sink_65": 40, "sink_66": 40}
    engine._mux_input_snapshot_t = 10.0
    # The flow ladder rides the same line (U30 campaign 5: the dead runs print no
    # ``CTRL output: ... since PLAYING`` line at all -- the EOS line is the only
    # one their log has -- so this is where the ladder has to be readable).
    engine._chain_input_pads = {
        ("sel", "video"): _FakeDiagnosticPad("sel_src", recorder),
        ("sel", "audio"): _FakeDiagnosticPad("asel_src", recorder),
        ("queue", "video"): _FakeDiagnosticPad("vq_src", recorder),
        ("queue", "audio"): _FakeDiagnosticPad("aq_src", recorder),
    }
    engine._chain_input_buffers = {
        ("sel", "video"): 161,
        ("sel", "audio"): 226,
        ("queue", "video"): 161,
        ("queue", "audio"): 226,
    }
    engine._chain_input_snapshot = {
        ("sel", "video"): 161,
        ("sel", "audio"): 40,
        ("queue", "video"): 161,
        ("queue", "audio"): 40,
    }

    assert engine._on_bus(None, _EosMessage()) is True

    err = capsys.readouterr().err
    assert "CTRL output: pipeline EOS from mpegtsmux_3" in err
    # The interval counters ride along, so the line says what was still flowing
    # when the output ended -- video silent, audio at its full rate here.
    assert "video=+0 audio=+234]" in err
    assert "[chain-in " in err
    assert "sel video=+0 audio=+186 | queue video=+0 audio=+186]" in err
    assert loop.quits == 1


# -- U30 EOS-origin diagnostic: WHERE the EOS that ends output entered --------
#
# The U30 off-live campaigns produce a shape no other line explains: the outgoing
# leg streamed its whole 4.05 s through ``sel``/``queue`` into the mux, the worker
# then quit cleanly on a bus EOS, and not one of the reload's own guards printed
# -- no ``outgoing-EOS-dropped``, no ``firing``, no commit. Either the EOS crossed
# a selector sink pad the boundary probe was not on, or it was generated
# downstream of every probe. Those are opposite fixes. One report-only observer on
# each already-counted pad answers it: the arrival labels render in data order on
# the EOS line, and ``none`` is itself a finding.


def test_u30_eos_observers_arm_after_the_counters_never_instead_of_them(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The observer must never take the counter's place on a pad.

    Same-priority pad probes run in installation order, and every other U30
    reader assumes ``probes[0]`` is the flow counter (the stall watchdog reads
    the mux src counter's pad, the ladder reads its rungs). An observer armed
    FIRST would make those reads return an observer, which silently reports
    nothing instead of failing -- the worst possible failure mode for a
    diagnostic. So: counter at index 0, observer at index 1, on every pad."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = _u30_armed_chain(engine, recorder)

    for key, pad in pads.items():
        assert len(pad.probes) == 2, (key, pad.probes)
        assert pad.probes[0][1].__name__ == "_count_chain_input", (key, pad.probes)
        assert pad.probes[1][0] == engine_module.Gst.PadProbeType.EVENT_DOWNSTREAM
        assert pad.probes[1][1].__name__ == "_observe_eos", (key, pad.probes)
    assert capsys.readouterr().err == ""

    # The mux src pad carries the stall watchdog's progress signal, so the same
    # ordering rule applies there.
    class _MuxWithSrc:
        """``_install_output_counter`` reads only ``get_static_pad("src")``."""

        def __init__(self, src: Any) -> None:
            self._src = src

        def get_static_pad(self, pad_name: str) -> Any:
            return self._src if pad_name == "src" else None

    mux_src = _FakeDiagnosticPad("mux_src", recorder)
    engine.mux = _MuxWithSrc(mux_src)
    engine._install_output_counter()

    assert [callback.__name__ for _mask, callback in mux_src.probes] == [
        "_count",
        "_observe_eos",
    ], mux_src.probes


def test_u30_an_eos_observer_records_where_the_eos_came_from(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Firing one observer appends ITS label -- and only that one.

    The observer is report-only: it returns OK, it never DROPs, and it never
    prints a line of its own. The EOS it watches for is the one about to quit
    the worker anyway, so a diagnostic that could alter the data path here would
    be able to take a channel off air to watch it die."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = _u30_armed_chain(engine, recorder)
    observer = pads[("queue", "audio")].probes[1][1]
    engine._eos_arrivals = []
    engine._eos_arrivals_overflow = 0

    assert engine._eos_arrivals == []
    eos_info = _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS))
    assert observer(None, eos_info) == engine_module.Gst.PadProbeReturn.OK
    assert engine._eos_arrivals == ["queue:audio"]
    assert engine._eos_arrival_suffix() == " [eos-arrivals: queue:audio]"

    # A pad that is observed but never fired must not appear: absence on this
    # line is "we watched and saw nothing", which is what makes ``none`` mean
    # something when EVERY observer is silent.
    assert "sel:video" not in engine._eos_arrival_suffix()
    assert capsys.readouterr().err == ""


def test_u30_an_eos_observer_records_only_an_eos(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """A caps/segment/tag event crossing an observed pad is NOT an arrival.

    An EVENT_DOWNSTREAM probe fires for every downstream event on its pad, so an
    observer that records unconditionally fills ``eos-arrivals`` with the startup
    event stream of whatever leg last crossed it. The U30 campaign that shipped
    that build read back ``[eos-arrivals: sel:audio, queue:audio, ... +186 more]``
    on 4 s runs -- a clause named ``eos-arrivals`` reporting events that are not
    EOSes, in a diagnostic whose entire product is the answer to "where did the
    EOS enter?". Recording nothing is the honest output for a non-EOS event: the
    line then still says ``none``, which is a claim about the pipeline, instead of
    a label that is merely a claim about traffic."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = _u30_armed_chain(engine, recorder)
    observer = pads[("queue", "audio")].probes[1][1]
    engine._eos_arrivals = []
    engine._eos_arrivals_overflow = 0

    for event_type in (_FakeEventType.SEGMENT, "CAPS", "STREAM_START", "TAG"):
        info = _FakeEventProbeInfo(_FakeProbeEvent(event_type))
        assert observer(None, info) == engine_module.Gst.PadProbeReturn.OK

    assert engine._eos_arrivals == []
    assert engine._eos_arrival_suffix() == " [eos-arrivals: none]"

    # Probe info that carries no event at all (the U16 buffer-only fixture, and
    # the ``None`` a hand-fired probe passes): unreadable is not an arrival, so
    # the observer must not invent one.
    assert observer(None, None) == engine_module.Gst.PadProbeReturn.OK
    assert observer(None, _FakeEventProbeInfo(None)) == engine_module.Gst.PadProbeReturn.OK
    assert engine._eos_arrivals == []

    # And a real EOS on the same observer still lands, so the filter is a filter
    # and not a mute.
    assert observer(None, _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS))) == (
        engine_module.Gst.PadProbeReturn.OK
    )
    assert engine._eos_arrivals == ["queue:audio"]
    assert capsys.readouterr().err == ""


def test_u30_eos_arrivals_fold_a_repeat_and_stay_bounded(engine_module) -> None:
    """Two observers on one pad see ONE arrival, and the list cannot grow forever.

    A superseded reload re-arms the observer on the same outgoing selector sink
    pad, so a single EOS there is observed twice -- reporting it twice would read
    as two arrivals. And a channel that emits many EOSes over a long run must not
    be able to grow an unbounded list in the worker's memory."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine._eos_arrivals = []
    engine._eos_arrivals_overflow = 0

    engine._record_eos_arrival("out:video")
    engine._record_eos_arrival("out:video")
    engine._record_eos_arrival("sel:video")
    engine._record_eos_arrival("sel:video")

    assert engine._eos_arrivals == ["out:video", "sel:video"]
    assert engine._eos_arrivals_overflow == 0

    for index in range(engine._EOS_ARRIVAL_MAX + 5):
        engine._record_eos_arrival(f"mux-sink:{index}")

    # Two labels are already in the list, so of the 17 more arrivals only the
    # first 10 fit: 12 on the line, 7 counted as overflow.
    assert len(engine._eos_arrivals) == engine._EOS_ARRIVAL_MAX
    assert engine._eos_arrivals_overflow == 7
    assert engine._eos_arrival_suffix().endswith(", +7 more]")


def test_u30_the_eos_line_says_where_the_eos_entered_or_that_nothing_did(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """``none`` is the finding, not a missing line.

    The two shapes the U30 campaigns cannot tell apart -- "the outgoing leg's own
    end escaped a guard that was not watching it" and "something inside the
    output half emitted EOS on its own" -- are separated by exactly this: a
    rendered label names the furthest downstream pad the EOS reached, and
    ``none`` says every observed pad watched and saw nothing, so the EOS was born
    below all of them."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)

    class _QuitCounter:
        def __init__(self) -> None:
            self.quits = 0

        def quit(self) -> None:
            self.quits += 1

    class _EosMessage:
        type = engine_module.Gst.MessageType.EOS
        src = _FakeDiagnosticPad("mpegtsmux_3", recorder)

    engine._loop = _QuitCounter()
    assert engine._on_bus(None, _EosMessage()) is True
    assert "[eos-arrivals: none]" in capsys.readouterr().err

    engine._record_eos_arrival("out:video")
    engine._record_eos_arrival("sel:video")
    engine._record_eos_arrival("queue:video")
    engine._record_eos_arrival("mux-sink:video")
    engine._record_eos_arrival("mux-src")
    engine._loop = _QuitCounter()
    assert engine._on_bus(None, _EosMessage()) is True

    err = capsys.readouterr().err
    assert "[eos-arrivals: out:video, sel:video, queue:video, mux-sink:video, mux-src]" in err, err
    # The U16/U30 clauses still ride the same line, unchanged.
    assert "CTRL output: pipeline EOS from mpegtsmux_3 -- quitting the worker [" in err


def test_u30_each_selector_sink_pad_is_named_by_its_own_eos_observer(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The EOS crossed a sink pad the ``out:*`` labels cannot name -- so name them ALL.

    ``out:video``/``out:audio`` are armed on ``selector_sink_pads[0]`` /
    ``audio_sink_pads[0]``, the pads the outgoing leg is EXPECTED to deliver on.
    Ten of the eleven deferred deaths of the 2026-09-25 off-live campaign (see
    the ``eos-arrivals`` clause) recorded an EOS at the selector's OWN src pad
    with no arrival on either of them -- and an ``input-selector`` only pushes an
    EOS downstream after one reached a sink pad, so the EOS crossed a sink pad
    that carries no label. A label that cannot name the pad cannot name the
    cause: this observer names each pad after itself.

    The two pads that already carry ``out:*`` are skipped, so one EOS never
    renders under two labels and the existing clause keeps its shape."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = {
        "out_video": _FakeDiagnosticPad("sink_0v", recorder),
        "other_video": _FakeDiagnosticPad("sink_1v", recorder),
        "new_video": _FakeDiagnosticPad("sink_2v", recorder),
        "out_audio": _FakeDiagnosticPad("sink_0a", recorder),
        "other_audio": _FakeDiagnosticPad("sink_1a", recorder),
        "new_audio": _FakeDiagnosticPad("sink_2a", recorder),
    }
    engine.selector_sink_pads = [pads["out_video"], pads["other_video"], None]
    engine.audio_sink_pads = [pads["out_audio"], pads["other_audio"]]
    engine._eos_arrivals = []
    engine._eos_arrivals_overflow = 0

    engine._install_selector_sink_eos_observers(
        extra=(("video", pads["new_video"]), ("audio", pads["new_audio"])),
        skip=(pads["out_video"], pads["out_audio"]),
    )

    for key in ("other_video", "other_audio", "new_video", "new_audio"):
        pad = pads[key]
        assert f"add_probe:{pad.name}:4:_observe_eos" in recorder.calls, recorder.calls
    for key in ("out_video", "out_audio"):
        pad = pads[key]
        assert f"add_probe:{pad.name}:4:_observe_eos" not in recorder.calls, recorder.calls

    observer = pads["other_video"].probes[0][1]
    assert (
        observer(pads["other_video"], _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)))
        == engine_module.Gst.PadProbeReturn.OK
    )
    assert engine._eos_arrivals == ["sink-pad:sink_1v:video"]
    assert engine._eos_arrival_suffix() == " [eos-arrivals: sink-pad:sink_1v:video]"

    # Same filter as every other observer: a segment crossing the pad is not an
    # arrival, so a named sink pad cannot manufacture one either.
    observer = pads["new_audio"].probes[0][1]
    assert (
        observer(pads["new_audio"], _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.SEGMENT)))
        == engine_module.Gst.PadProbeReturn.OK
    )
    assert engine._eos_arrivals == ["sink-pad:sink_1v:video"]
    assert capsys.readouterr().err == ""


def test_u30_a_selector_src_arrival_names_the_active_sink_pad(engine_module) -> None:
    """``sel:video`` says the selector emitted an EOS; ``active`` says from where.

    Which input the selector was pointed at when it forwarded that EOS is what
    separates the two readings of a deferred death -- the EOS came out of the
    leg that was still selected, or the selector had already been pointed
    elsewhere -- and it is one property read at EOS time, not a new probe.

    A selector whose ``active-pad`` cannot be read renders the bare ``sel:video``
    the line rendered before: silence about the pad, never a guess at it."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pads = _u30_armed_chain(engine, recorder)
    engine._eos_arrivals = []
    engine._eos_arrivals_overflow = 0
    engine.selector.active_pad = _FakeDiagnosticPad("sink_0v", recorder)

    observer = pads[("sel", "video")].probes[1][1]
    assert (
        observer(pads[("sel", "video")], _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)))
        == engine_module.Gst.PadProbeReturn.OK
    )
    assert engine._eos_arrivals == ["sel:video[active=sink_0v]"]

    # Unreadable active pad -> the un-annotated label, not an invented one.
    engine.selector.active_pad = None
    assert (
        observer(pads[("sel", "video")], _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)))
        == engine_module.Gst.PadProbeReturn.OK
    )
    assert engine._eos_arrivals == ["sel:video[active=sink_0v]", "sel:video"]

    # A selector that cannot answer at all is the same case, and the OTHER rungs
    # keep their plain labels.
    class _NoProperties:
        def get_static_pad(self, pad_name: str) -> Any:
            return pads[("sel", "video")]

    engine.selector = _NoProperties()
    engine._chain_input_pads = {}
    engine._chain_input_buffers = {}
    engine._install_chain_input_counters()
    assert engine._chain_input_pads[("sel", "video")] is pads[("sel", "video")]
    engine._eos_arrivals = []
    observer = pads[("sel", "video")].probes[-1][1]
    assert (
        observer(pads[("sel", "video")], _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)))
        == engine_module.Gst.PadProbeReturn.OK
    )
    assert engine._eos_arrivals == ["sel:video"]
    # The re-armed ladder still observes the OTHER rungs -- this selector losing
    # its property read did not disarm anything else.
    queue_observer = pads[("queue", "audio")].probes[-1][1]
    assert (
        queue_observer(
            pads[("queue", "audio")], _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS))
        )
        == engine_module.Gst.PadProbeReturn.OK
    )
    assert engine._eos_arrivals == ["sel:video", "queue:audio"]


def test_u30_inbound_counters_count_the_new_leg_at_its_selector_sink_pad(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rung ``in``: did the switched-in leg KEEP pushing after the switch?

    Rungs ``sel`` and ``queue`` can only say where the data stopped being
    forwarded; ``sel video=+0`` after a switch is either "the leg's producer
    stopped pushing" or "the selector swallowed what it pushed". ``in`` counts
    the leg's buffers ARRIVING at its selector request sink pad -- the pad the
    ``new-leg-selector-first-buffer`` diagnostic already reports ONCE -- so
    ``in +N, sel +0`` names the selector and ``in +0, sel +0`` names the
    producer. Renders first (upstream to downstream), which is what makes that
    reading order possible."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    _u30_armed_chain(engine, recorder)
    inbound = {
        "video": _FakeDiagnosticPad("new_sel_sink_video", recorder),
        "audio": _FakeDiagnosticPad("new_sel_sink_audio", recorder),
    }

    engine._install_new_leg_inbound_counters(
        {"new_video_pad": inbound["video"], "new_audio_pad": inbound["audio"]}
    )

    assert engine._chain_input_pads[("in", "video")] is inbound["video"]
    assert engine._chain_input_pads[("in", "audio")] is inbound["audio"]
    assert engine._chain_input_buffers[("in", "video")] == 0
    assert engine._chain_input_buffers[("in", "audio")] == 0
    for pad in inbound.values():
        assert f"add_probe:{pad.name}:1:_count_chain_input" in recorder.calls, recorder.calls
    assert capsys.readouterr().err == ""

    # Each rung counts its own stream only, same contract as ``sel``/``queue``.
    _mask, callback = inbound["audio"].probes[0]
    for _ in range(2):
        assert (
            callback(inbound["audio"], _FakeProbeInfo(_FakeProbeBuffer(0)))
            == engine_module.Gst.PadProbeReturn.OK
        )
    assert engine._chain_input_buffers[("in", "audio")] == 2
    assert engine._chain_input_buffers[("in", "video")] == 0

    engine._chain_input_snapshot = dict.fromkeys(engine._chain_input_pads, 0)
    assert engine._chain_input_delta_suffix(3.0).startswith(
        " [chain-in 3.0s: in video=+0 audio=+2 | sel "
    )


def test_u30_inbound_counters_skip_a_pad_that_cannot_be_counted(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same absence rule as every other rung: uncountable is ABSENT, never ``+0``.

    A missing ``new_audio_pad`` (a reload whose audio leg never got a selector
    pad) and a pad that refuses the probe must both leave the rung out -- a
    registered-but-uncountable rung would read ``+0`` forever and accuse a
    healthy stream of having stopped."""
    recorder = _Recorder()

    class _RefusingPad(_FakeDiagnosticPad):
        def add_probe(self, mask: Any, callback: Any) -> str:
            raise RuntimeError("probe refused")

    engine = _bare_engine_for_commit(engine_module, recorder)
    engine._chain_input_pads = {("sel", "video"): _FakeDiagnosticPad("sel_src", recorder)}
    engine._chain_input_buffers = {("sel", "video"): 0}

    engine._install_new_leg_inbound_counters(
        {
            "new_video_pad": _RefusingPad("refusing_sel_sink", recorder),
            "new_audio_pad": None,
        }
    )

    assert not [key for key in engine._chain_input_pads if key[0] == "in"]
    assert not [key for key in engine._chain_input_buffers if key[0] == "in"]
    assert ("sel", "video") in engine._chain_input_pads
    assert capsys.readouterr().err == ""


def test_u30_inbound_counters_are_skipped_when_no_ladder_was_ever_armed(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rung ``in`` JOINS a ladder; it must never require one to exist.

    ``_install_chain_input_counters`` arms ``_chain_input_pads`` /
    ``_chain_input_buffers`` once per worker; this method only adds a key to
    them. An engine that never armed the ladder therefore has no rung ``in`` to
    add -- and no ladder to render, since ``_chain_input_delta_suffix`` already
    renders a missing rung as absent rather than ``+0``. The shape is real, not
    hypothetical: the U16 real-runtime emitter arms the selector-side
    diagnostics on a bare ``object.__new__(GstPlayoutEngine)`` (no ``__init__``,
    no pipeline), which is where the unguarded write raised
    ``AttributeError: 'GstPlayoutEngine' object has no attribute
    '_chain_input_pads'`` and took the whole emit down with it.
    """
    engine = object.__new__(engine_module.GstPlayoutEngine)

    engine._install_new_leg_inbound_counters(
        {"new_video_pad": _FakeDiagnosticPad("stub_sel_sink_video", _Recorder())}
    )

    assert not hasattr(engine, "_chain_input_pads")
    assert not hasattr(engine, "_chain_input_buffers")
    assert capsys.readouterr().err == ""


def test_u30_reset_stall_reference_rebaselines_the_flow_ladder(engine_module) -> None:
    """A committed reload starts the diagnostic interval, so the numbers that
    follow it are POST-COMMIT numbers.

    MEASURED off-live (campaign 6, immediate switch, dead runs): the run's only
    line was the pipeline-EOS line reading ``[mux-in 4.1s: video=+121 ...]`` and
    ``[chain-in 4.1s: sel video=+126 audio=+142 | queue video=+120 audio=+187]``
    -- a 4.1 s interval that STARTS at arm time and therefore straddles the
    commit, so it reads the healthy outgoing leg's flow plus the 2.2 s retiring
    tail and says nothing about the leg that was switched in. Anchoring the
    interval at the commit turns the same line into ``+0`` for a leg that never
    fed the mux. Only the interval moves: the stall watchdog reads
    ``_output_buffers``, not these counters."""
    engine = _bare_engine_for_commit(engine_module, _Recorder())
    engine._mux_input_pads = {
        "sink_65": _FakeDiagnosticPad("sink_65", _Recorder(), caps="video/x-h264"),
    }
    engine._mux_input_buffers = {"sink_65": 900}
    engine._mux_input_snapshot = {"sink_65": 779}
    engine._chain_input_pads = {
        ("in", "video"): _FakeDiagnosticPad("new_sel_sink_video", _Recorder()),
        ("sel", "video"): _FakeDiagnosticPad("sel_src", _Recorder()),
        ("queue", "video"): _FakeDiagnosticPad("vq_src", _Recorder()),
    }
    engine._chain_input_buffers = {
        ("in", "video"): 0,
        ("sel", "video"): 130,
        ("queue", "video"): 120,
    }
    engine._chain_input_snapshot = {("in", "video"): 0, ("sel", "video"): 4, ("queue", "video"): 4}

    assert engine._mux_input_delta_suffix(4.1) == (
        " [mux-in 4.1s: video=+121] [chain-in 4.1s: in video=+0 | sel video=+126 "
        "| queue video=+116]"
    )

    engine._reset_stall_reference()

    assert engine._mux_input_delta_suffix(0.5) == (
        " [mux-in 0.5s: video=+0] [chain-in 0.5s: in video=+0 | sel video=+0 | queue video=+0]"
    )
    assert engine._stall_last_advance_t > 0.0


# ---------------------------------------------------------------------------
# U30 / Work 2 -- the outgoing leg's EOS must be caught DURING the build
# ---------------------------------------------------------------------------
#
# OBSERVED, ``%TEMP%\u30\campaign11\run00\worker.log`` (GST_DEBUG=
# input-selector:7,concat:7): the concat sub-chain boundary EOS crossed the
# selector's ACTIVE sink pad while ``reload_program`` was still building the new
# leg, because the pending transaction and its boundary DROP probes used to be
# installed only AFTER ``_instantiate_source_leg``/``_link_leg_to_selectors``
# returned:
#
#   0:00:02.193483700 gst_concat_sink_event:<vconcat_program_1:sink_1> received eos
#   0:00:02.194962100 gst_concat_switch_pad:<vconcat_program_1> Switching
#   0:00:02.195077700 gst_selector_pad_event:<sel:sink_0> received EOS
#   0:00:02.196030700 gst_input_selector_eos_wait:<sel:sink_0> send EOS event
#   0:00:02.441608800 CTRL reload: new leg stream held at its first buffer ...
#   0:00:02.449068400 CTRL reload: new leg preroll verified ...
#   0:00:02.452308800 CTRL output: pipeline EOS ... quitting the worker
#
# The EOS crossed 0.25 s BEFORE the reload armed anything that would have
# dropped it. It reached mpegtsmux, then the bus, then ``_announce_pipeline_eos``
# -- and the worker quit with ``teardown_clean=True``, ``reload-status.json``
# ``aborted:stopped``, and no commit stage ever printed: output stops for good.
# ``run01`` of the same campaign ends its leg at the same wall instant and
# commits, because there the probes were armed in time. One race, two outcomes.
#
# The repair is ordering, not new machinery: transaction identity, the pending
# slot and the boundary DROP probes are installed BEFORE the build, so no EOS
# can cross unguarded; the new-leg fields are filled in afterwards. Nothing in
# ``_on_old_leg_eos`` touches a new-leg field and ``_on_new_leg_ready`` commits
# immediately when the boundary was already seen, so an EOS recorded during the
# build turns this shape into the ordinary healthy deferred switch.


class _StopBuild(Exception):
    """Freeze ``reload_program`` at the instant it is inside the build."""


class _StubLeg:
    """A replacement program good enough for ``source_leg_is_clock_timed``.

    Empty ``elements``/``audio`` make that helper answer "segment-timed" without
    touching GStreamer, so the reload takes the finite/held path -- the path
    every deferred death in the off-live campaign was on."""

    label = "program"
    elements: tuple[Any, ...] = ()
    audio: tuple[Any, ...] = ()


@pytest.mark.parametrize("refusal", ["failure", "raise"])
def test_reload_state_sync_failure_aborts_without_starting_remaining_elements(
    engine_module, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    """A failed or raising state change must unwind the incoming leg immediately."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine._reload_txn_counter = count(1)
    engine._plan_eos_held = False
    engine.reload_timeout_s = 10
    monkeypatch.setattr(engine_module.Gst.PadProbeType, "BLOCK", 16, raising=False)
    monkeypatch.setattr(
        engine_module.GLib, "timeout_add_seconds", lambda *_args: 123, raising=False
    )
    old_video = _FakeOldPad("old-video", recorder, None)
    old_audio = _FakeOldPad("old-audio", recorder, None)
    old_elements = [_FakeOldElement("outgoing", recorder)]
    engine.selector_sink_pads = [old_video]
    engine.audio_sink_pads = [old_audio]
    engine._source_leg_elements = [old_elements]
    video = _FakeOldPad("incoming-video", recorder, None)
    audio = _FakeOldPad("incoming-audio", recorder, None)

    class _IncomingElement(_FakeOldElement):
        def sync_state_with_parent(self) -> bool:
            return self.set_state(engine.pipeline.state) != _FakeStateChangeReturn.FAILURE

        def set_state(self, _state: Any) -> str:
            recorder.calls.append(f"sync:{self.name}")
            if self.name == "refused":
                if refusal == "raise":
                    raise RuntimeError("refused")
                return _FakeStateChangeReturn.FAILURE
            return _FakeStateChangeReturn.SUCCESS

    incoming = [_IncomingElement(name, recorder) for name in ("later", "refused", "started")]
    monkeypatch.setattr(engine, "_instantiate_source_leg", lambda _leg: (video, audio, incoming))
    monkeypatch.setattr(engine, "_link_leg_to_selectors", lambda *_args: (video, audio))
    retired: list[list[Any]] = []
    monkeypatch.setattr(
        engine,
        "_start_aborted_leg_retirement",
        lambda _reason, _video, _audio, elements, **_kwargs: retired.append(elements),
    )
    settled: list[tuple[bool, str | None]] = []

    with pytest.raises(RuntimeError, match="refused"):
        engine.reload_program(
            _StubLeg(),
            switch_at_end_of_current=True,
            on_settled=lambda applied, reason: settled.append((applied, reason)),
        )

    assert [call for call in recorder.calls if call.startswith("sync:")] == [
        "sync:started",
        "sync:refused",
    ]
    assert engine._pending_reload is None
    assert settled == [(False, "build-error")]
    assert retired == [incoming]
    assert engine.selector_sink_pads == [old_video]
    assert engine.audio_sink_pads == [old_audio]
    assert engine._source_leg_elements == [old_elements]
    assert not any(call.startswith("set_state:outgoing:") for call in recorder.calls)
    assert any(call.startswith("remove_probe:incoming-video:") for call in recorder.calls)
    assert any(call.startswith("remove_probe:incoming-audio:") for call in recorder.calls)


@pytest.mark.parametrize("playlist", [False, True], ids=["source-leg", "playlist-concats"])
@pytest.mark.parametrize("decoder_async", [False, True], ids=["immediate", "decoder-async"])
def test_reload_activates_downstream_before_immediate_producers(
    engine_module, monkeypatch: pytest.MonkeyPatch, playlist: bool, decoder_async: bool
) -> None:
    """A producer can start inside sync, before the next element is activated.

    Model the resulting FLUSHING as a stopped producer, despite successful state
    sync. Playlist concats are constructed before their feeder chains, so merely
    reversing the complete construction list would still fail that shape. Async
    decoder preroll must not change the target applied to its live upstream source.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine._reload_txn_counter = count(1)
    engine._plan_eos_held = False
    engine.reload_timeout_s = 10
    monkeypatch.setattr(engine_module.Gst.PadProbeType, "BLOCK", 16, raising=False)
    monkeypatch.setattr(
        engine_module.GLib, "timeout_add_seconds", lambda *_args: 123, raising=False
    )
    engine.selector_sink_pads = [_FakeOldPad("old-video", recorder, None)]
    engine.audio_sink_pads = [_FakeOldPad("old-audio", recorder, None)]
    engine._source_leg_elements = [[]]

    class _IncomingElement(_FakeOldElement):
        def __init__(self, name: str, downstream: Any = None, *, producer: bool = False):
            super().__init__(name, recorder)
            self.downstream = downstream
            self.producer = producer
            self.active = False
            self.first_buffer_delivered = False

        def sync_state_with_parent(self) -> bool:
            # decodebin can put the parent into PAUSED/PAUSED while it awaits
            # input. Sampling it again strands a live upstream source there.
            state = engine.pipeline.pending_state
            if state == _FakeState.VOID_PENDING:
                state = engine.pipeline.state
            return self.set_state(state) != _FakeStateChangeReturn.FAILURE

        def set_state(self, state: Any) -> str:
            self.active = True
            recorder.calls.append(f"sync:{self.name}")
            if self.name == "decodebin" and decoder_async:
                engine.pipeline.state = _FakeState.PAUSED
                engine.pipeline.pending_state = _FakeState.PAUSED
            if self.producer:
                if state != _FakeState.PLAYING:
                    recorder.calls.append(f"waiting-for-playing:{self.name}")
                    return _FakeStateChangeReturn.NO_PREROLL
                downstream = self.downstream
                while downstream is not None:
                    if not downstream.active:
                        recorder.calls.append(f"flushing:{self.name}")
                        return _FakeStateChangeReturn.SUCCESS
                    downstream = downstream.downstream
                self.first_buffer_delivered = True
            return _FakeStateChangeReturn.SUCCESS

    video_concat = _IncomingElement("video-concat") if playlist else None
    audio_concat = _IncomingElement("audio-concat") if playlist else None
    video_tail = _IncomingElement("video-caps", video_concat)
    audio_tail = _IncomingElement("audio-caps", audio_concat)
    audio_convert = _IncomingElement("audio-convert", audio_tail)
    decoder = _IncomingElement("decodebin", video_tail)
    video_source = _IncomingElement("video-source", decoder, producer=True)
    audio_source = _IncomingElement("audio-source", audio_convert, producer=True)
    incoming = [video_source, decoder, video_tail, audio_source, audio_convert, audio_tail]
    if playlist:
        incoming = [video_concat, audio_concat, *incoming]
    video = _FakeOldPad("incoming-video", recorder, None)
    audio = _FakeOldPad("incoming-audio", recorder, None)
    video.parent_element = video_concat if playlist else video_tail
    audio.parent_element = audio_concat if playlist else audio_tail
    monkeypatch.setattr(engine, "_instantiate_source_leg", lambda _leg: (video, audio, incoming))
    monkeypatch.setattr(engine, "_link_leg_to_selectors", lambda *_args: (video, audio))

    engine.reload_program(_StubLeg(), switch_at_end_of_current=True)

    assert video_source.first_buffer_delivered
    assert audio_source.first_buffer_delivered
    assert not any(call.startswith("flushing:") for call in recorder.calls)
    assert all(element.active for element in incoming)
    assert len(engine._pending_reload["hold_probes"]) == 2


def test_u30_outgoing_eos_is_dropped_while_the_reload_is_still_building(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The boundary EOS is this transaction's to drop from the moment the build
    starts -- not from the moment the build finishes.

    A build is not instantaneous (0.25 s measured off-live on the reproducer),
    and the outgoing leg keeps streaming throughout it. An EOS that arrives in
    that window and is not dropped is forwarded by the selector, reaches the
    mux, and quits the worker: the exact ``teardown_clean`` death the campaign
    recorded. It must be dropped AND recorded, so the reload commits on the
    boundary it just observed instead of waiting for a boundary already past."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine._reload_txn_counter = count(1)
    old_video = _FakeOldPad("sink_0", recorder, None)
    old_audio = _FakeOldPad("sink_1", recorder, None)
    engine.selector_sink_pads = [old_video]
    engine.audio_sink_pads = [old_audio]
    # The boundary probes are the BUFFER|EVENT_DOWNSTREAM ones -- distinct from
    # the plain EVENT_DOWNSTREAM ``out:*`` observers armed just above them.
    boundary_mask = (
        engine_module.Gst.PadProbeType.BUFFER | engine_module.Gst.PadProbeType.EVENT_DOWNSTREAM
    )

    observed: dict[str, Any] = {}

    def _build(leg: Any) -> Any:
        pending = engine._pending_reload
        observed["pending_during_build"] = pending is not None
        observed["armed_during_build"] = (
            None if pending is None else len(pending["boundary_probes"])
        )
        observed["txn_during_build"] = None if pending is None else pending["txn_id"]
        armed = [probe for probe in old_video.probes if probe[1] == boundary_mask]
        observed["video_boundary_probes"] = len(armed)
        for _probe_id, _mask, callback, args in armed:
            # Fired the way the streaming thread fires it, from inside the build.
            observed["probe_return"] = callback(
                old_video, _FakeEventProbeInfo(_FakeProbeEvent(_FakeEventType.EOS)), *args
            )
        observed["old_leg_eos_during_build"] = None if pending is None else pending["old_leg_eos"]
        raise _StopBuild

    engine._instantiate_source_leg = _build  # type: ignore[method-assign]

    with pytest.raises(_StopBuild):
        engine.reload_program(_StubLeg(), switch_at_end_of_current=True)

    # While the build was still running, this transaction already owned the
    # outgoing pads: two boundary probes armed, and the EOS dropped + recorded.
    assert observed["pending_during_build"] is True, observed
    assert observed["armed_during_build"] == 2, observed
    assert observed["video_boundary_probes"] == 1, observed
    assert observed["probe_return"] == engine_module.Gst.PadProbeReturn.DROP, observed
    assert observed["old_leg_eos_during_build"] is False, observed
    err = capsys.readouterr().err
    assert (
        "CTRL reload diagnostic: outgoing-EOS-dropped pad=sink_0 stream=video pending_txn=1"
    ) in err
    assert "CTRL reload: outgoing EOS observed stream=video (1/2 stream(s))" in err

    # A build that RAISES must leave nothing behind. A DROP probe left installed
    # on the live outgoing pad would swallow that leg's real EOS forever -- the
    # channel would never switch at a boundary again.
    assert engine._pending_reload is None
    assert f"remove_probe:sink_0:sink_0-probe-{boundary_mask}" in recorder.calls, recorder.calls
    assert f"remove_probe:sink_1:sink_1-probe-{boundary_mask}" in recorder.calls, recorder.calls


def test_u30_a_failed_build_removes_the_probes_it_armed(
    engine_module, capsys: pytest.CaptureFixture[str]
) -> None:
    """The unwind is an unwind, not a half-started transaction.

    Hoisting the pending slot above the build is only safe if a build that
    raises restores the pre-call state exactly: no pending reload, and every
    boundary probe that was armed is removed again -- a DROP probe left on the
    live outgoing pad swallows that leg's real EOS forever. The caller still
    gets the original exception (its contract), so the failure is reported once
    and honestly, and the settle callback does not fire."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine._reload_txn_counter = count(1)
    old_video = _FakeOldPad("sink_0", recorder, None)
    old_audio = _FakeOldPad("sink_1", recorder, None)
    engine.selector_sink_pads = [old_video]
    engine.audio_sink_pads = [old_audio]
    boundary_mask = (
        engine_module.Gst.PadProbeType.BUFFER | engine_module.Gst.PadProbeType.EVENT_DOWNSTREAM
    )
    settled: list[tuple[bool, str | None]] = []

    def _build(_leg: Any) -> Any:
        raise RuntimeError("instantiate failed")

    engine._instantiate_source_leg = _build  # type: ignore[method-assign]

    def _on_settled(committed: bool, reason: str | None) -> None:
        settled.append((committed, reason))

    with pytest.raises(RuntimeError, match="instantiate failed"):
        engine.reload_program(_StubLeg(), switch_at_end_of_current=True, on_settled=_on_settled)

    armed = [
        (pad, probe_id)
        for pad in (old_video, old_audio)
        for probe_id, mask, _callback, _args in pad.probes
        if mask == boundary_mask
    ]
    assert len(armed) == 2, recorder.calls
    for pad, probe_id in armed:
        assert f"remove_probe:{pad.name}:{probe_id}" in recorder.calls, recorder.calls
    assert engine._pending_reload is None
    assert settled == []
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# U37 -- a deferred rebased switch waits for the mux sink pads to drain.
#
# The measured defect (off-live, real LPM prepared media, 2026-09-25): a mux
# sink pad runs about a second of chain latency behind the selector, so the
# outgoing leg's LAST queued buffer is still sitting on that pad when the
# rebased leg's SEGMENT arrives. ``gst_aggregator_default_sink_event`` copies
# the new segment into the pad immediately, but ``gst_base_ts_mux_clip``
# computes running time at POP -- under whatever segment is current THEN. One
# stale video buffer was therefore re-dated into the new timeline: running time
# 9.411322222s aired at 19.992322222s, and the next video frame stepped
# BACKWARDS to 10.581322222s.
#
# The guard is the drain wait in ``_defer_rebase_drain``. The arrivals
# themselves are only OBSERVED: dropping them until the segment crossed was
# measured to cost a 1.024s hole in the aired audio (1013 audio frames against
# the 1061 every non-dropping run airs), because an arrival that precedes the
# segment is still clipped under the old one and is legitimate outgoing tail.
# ---------------------------------------------------------------------------

_U37_OBSERVER_MASK = (
    _FakePadProbeType.BUFFER | _FakePadProbeType.BUFFER_LIST | _FakePadProbeType.EVENT_DOWNSTREAM
)


class _FakeDrainPad:
    """A mux sink pad with the aggregator's own readable queue depth.

    ``current-level-buffers`` is the predicate the production wait reads
    (``_rebase_drain_held``), so this fake models it as the mutable value it is
    on a live pad rather than as a call counter."""

    def __init__(self, name: str, caps: str, level: int, recorder: _Recorder) -> None:
        self.name = name
        self.caps = caps
        self.level = level
        self.recorder = recorder
        self.probes: list[tuple[str, Any, Any]] = []
        # The extra arguments a probe callback is bound with (the fence probes
        # take ``pending``); kept beside ``probes`` so the existing
        # ``(probe_id, mask, callback)`` unpacking in this file is unchanged.
        self.probe_args: list[tuple[Any, ...]] = []
        self.removed: list[Any] = []
        self._next_probe = 0

    def get_name(self) -> str:
        return self.name

    def get_current_caps(self) -> Any:
        return types.SimpleNamespace(to_string=lambda: self.caps)

    def get_property(self, key: str) -> Any:
        assert key == "current-level-buffers", key
        return self.level

    def add_probe(self, mask: Any, callback: Any, *args: Any) -> str:
        self._next_probe += 1
        probe_id = f"{self.name}-probe-{self._next_probe}"
        self.probes.append((probe_id, mask, callback))
        self.probe_args.append(args)
        return probe_id

    def remove_probe(self, probe_id: Any) -> None:
        self.removed.append(probe_id)


class _UnreadableDrainPad(_FakeDrainPad):
    """A pad whose ``current-level-buffers`` cannot be read at all."""

    def get_property(self, key: str) -> Any:
        raise RuntimeError("no such property")


class _FakePadProbeInfo:
    """A ``Gst.PadProbeInfo`` as one pad's own streaming thread sees it."""

    def __init__(self, probe_type: Any, event: Any = None) -> None:
        self.type = probe_type
        self._event = event

    def get_event(self) -> Any:
        return self._event


class _FakeSegmentEvent:
    def __init__(self, event_type: Any) -> None:
        self.type = event_type


class _RaisingProbeInfo:
    """Probe info whose ``type`` read raises -- the streaming-thread hazard."""

    @property
    def type(self) -> Any:
        raise RuntimeError("probe info unavailable")


def _u37_rebase_pending(
    recorder: _Recorder, *, txn_id: int = 41, audio_level: int = 5
) -> tuple[dict[str, Any], _FakeDrainPad, _FakeDrainPad]:
    """A deferred program->program rebase whose mux sink pads a test owns.

    Named hold pads (not ``object()``) so the selector mutation the switch
    performs is legible in the call recorder."""
    pending = _unready_reload(recorder, txn_id=txn_id)
    pending["commit_in_progress"] = True
    pending["commit_watchdog"] = None
    pending["commit_completed"] = None
    pending["retirement_result"] = None
    video = _FakeDrainPad("sink_65", "video/x-h264", 0, recorder)
    audio = _FakeDrainPad("sink_66", "audio/mpeg", audio_level, recorder)
    return pending, video, audio


def _u37_timeout_adds(
    monkeypatch: pytest.MonkeyPatch, engine_module: types.ModuleType
) -> list[Any]:
    """Capture ``GLib.timeout_add`` instead of running it.

    These tests have no main loop, and this file's fake GLib deliberately has
    no ``timeout_add`` at all -- so the callback is recorded here and fired by
    hand, in the order the loop would have fired it."""
    armed: list[tuple[int, Any, tuple[Any, ...]]] = []

    def _add(interval_ms: int, callback: Any, *args: Any) -> int:
        armed.append((interval_ms, callback, args))
        return len(armed)

    monkeypatch.setattr(engine_module.GLib, "timeout_add", _add, raising=False)
    return armed


def test_u37_deferred_rebase_switch_waits_for_the_mux_pad_to_drain(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The switch is held back while the audio pad still holds the outgoing
    tail, and completes on the first tick after that pad reports empty.

    RED at 5397c069: the selector was mutated inside ``_begin_reload_commit``
    itself, with the outgoing tail still queued on the pad -- the exact state
    that re-dated a stale frame by the rebase offset."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, video_pad, audio_pad = _u37_rebase_pending(recorder)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._pending_reload = pending
    armed = _u37_timeout_adds(monkeypatch, engine_module)

    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)

    assert "video_sel.set_property:active-pad=new-video" not in recorder.calls, recorder.calls

    # The observer deadline is armed FIRST, at the summed bound, and the poller
    # only because that succeeded: an observer no timer could remove would
    # outlive the transaction it describes.
    assert [(ms, callback.__name__) for ms, callback, _args in armed] == [
        (4000, "_on_rebase_observer_deadline"),
        (20, "_resume_rebase_drain"),
    ], armed
    assert pending["rebase_drain_timeout_id"] == 2
    assert pending["rebase_observer_pads"] == [
        ("video", video_pad, "sink_65"),
        ("audio", audio_pad, "sink_66"),
    ]
    assert [(mask, callback.__name__) for _id, mask, callback in video_pad.probes] == [
        (_U37_OBSERVER_MASK, "_observe_rebase_arrivals")
    ]
    assert [(mask, callback.__name__) for _id, mask, callback in audio_pad.probes] == [
        (engine_module.Gst.PadProbeType.BUFFER, "_mux_tail_cutoff_probe"),
        (engine_module.Gst.PadProbeType.EVENT_DOWNSTREAM, "_mux_tail_segment_probe"),
        (_U37_OBSERVER_MASK, "_observe_rebase_arrivals"),
    ]

    err = capsys.readouterr().err
    assert (
        "CTRL reload diagnostic: stage=rebase-observer-armed pads=sink_65,sink_66 "
        "observed=sink_65,sink_66 reload_id=41"
    ) in err
    # Only the pad that is actually holding is named -- the drain set is a
    # predicate reading, not a list of the pads this switch touches.
    assert "CTRL reload diagnostic: stage=rebase-drain-wait pads=sink_66=5 reload_id=41" in err

    # A tick while the pad still holds switches nothing.
    assert engine._resume_rebase_drain(pending) is True
    assert "video_sel.set_property:active-pad=new-video" not in recorder.calls, recorder.calls

    # The pad empties; the next tick completes the commit, in the order the
    # U30/U34 handover tests pin: selector mutation, then hold release.
    audio_pad.level = 0
    assert engine._resume_rebase_drain(pending) is False
    calls = recorder.calls
    assert "video_sel.set_property:active-pad=new-video" in calls, calls
    assert "audio_sel.set_property:active-pad=new-audio" in calls, calls
    assert _index_of(calls, "video_sel.set_property:active-pad=new-video") < _index_of(
        calls, "remove_probe:new-video:1"
    ), calls

    drained_lines = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("CTRL reload diagnostic: stage=rebase-drain-drained")
    ]
    assert len(drained_lines) == 1, drained_lines
    assert drained_lines[0].endswith("s reload_id=41"), drained_lines[0]
    waited = float(drained_lines[0].split("waited=")[1].split("s ")[0])
    assert 0.0 <= waited < 1.0, drained_lines[0]


def test_u37_drain_deadline_switches_anyway_and_names_the_pad(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A guard whose premise has failed must say so and switch anyway.

    Holding the stream off air forever is the one outcome worse than the
    re-dating: the deadline names the pad that never emptied."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, video_pad, audio_pad = _u37_rebase_pending(recorder, txn_id=42)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._pending_reload = pending
    _u37_timeout_adds(monkeypatch, engine_module)

    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)
    capsys.readouterr()

    pending["rebase_drain_started_t"] = time.monotonic() - 3.5
    assert engine._resume_rebase_drain(pending) is False

    assert "video_sel.set_property:active-pad=new-video" in recorder.calls, recorder.calls
    assert (
        "WARN: rebase drain did not empty within 3.0s for reload 42 "
        "(sink_66=5 still queued at the mux); switching anyway -- a stale buffer "
        "may still be re-dated by the segment"
    ) in capsys.readouterr().err


def test_rc61_drain_waits_for_the_loudness_window_to_flush_the_retiring_audio_tail(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A mux pad emptied by the loudness element is not a pad that has aired its tail.

    MEASURED on the 5060 Ti, rc.6, 2026-11-17/18 (reload id=1, public, 01:12:24Z):
    ``stage=rebase-drain-drained waited=1.812s`` was followed 16s later by
    ``CTRL stall: audio running time 3.0s behind video for 10s (last reload id=1
    stage=committed)``. The same commit's own fence line names the shortfall
    exactly: ``stage=mux-tail-wait seen={'sink_66': 401173333333}
    target={'sink_66': 404181333332}`` -- 3.008s of retiring audio that had not
    reached the mux, against ``audio_end=404.181`` measured at the selector.

    3.008s is the declared per-output loudness latency
    (``audioloudnorm`` buffers a 3s BS.1770 window -- ``bridge.py`` builds
    ``audio_latency_ns`` from it, and the graph already carries it so the drain
    can wait for emitted tails). At the drain poll the pad's
    ``current-level-buffers`` is 0 because the element has not EMITTED the tail
    yet, so the drain's predicate ("this pad has handed over everything it had
    taken") is true while a full loudness window of the retiring leg is still
    inside the chain. The commit then switches, the changeover SEGMENT crosses
    the audio pad ahead of that tail, and the tail arrives afterwards -- clipped,
    at POP time, under the NEW base. Only the VIDEO pad has an arrival guard
    against a future-dated arrival; the audio pad has none, so the re-dated tail
    sits at the head of the pad, the new leg's audio queues behind it, and audio
    stops being consumed while video keeps airing. That is the watchdog line.

    RED without the fix: the drain names no held pad and the selector switches on
    the first pass even though the audio mux pad is a loudness window short of the
    outgoing leg's own measured end.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    # audio_level=0 is the measured state: the pad is empty because the element
    # downstream of the selector still holds the tail.
    pending, video_pad, audio_pad = _u37_rebase_pending(recorder, txn_id=51, audio_level=0)
    old_audio = _FakeOldPad("old-audio", recorder, None)
    pending["old_audio_pad"] = old_audio
    # The two FINAL outgoing ends this commit rebases onto, keyed by pad identity
    # exactly as ``_reload_outgoing_ends_in_order`` reads them.
    pending["outgoing_end"] = {
        pending["old_video_pad"]: {"end": 404_200_000_000, "segment": None},
        old_audio: {"end": 404_181_333_332, "segment": None},
    }
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    # What the mux has AIRED off each pad, straight from the run's own fence line.
    engine._mux_pad_airing_rt = {
        "sink_65": 404_166_666_666,
        "sink_66": 401_173_333_333,
    }
    engine.output_audio_latency_ns = 3_000_000_000
    engine._pending_reload = pending
    armed = _u37_timeout_adds(monkeypatch, engine_module)

    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)

    assert "video_sel.set_property:active-pad=new-video" not in recorder.calls, (
        "the switch must not run while the loudness window still holds the "
        f"retiring audio tail; calls={recorder.calls}"
    )
    assert [callback.__name__ for _ms, callback, _args in armed] == [
        "_on_rebase_observer_deadline",
        "_resume_rebase_drain",
    ], armed
    err = capsys.readouterr().err
    assert (
        "CTRL reload diagnostic: stage=rebase-drain-wait pads=sink_66=tail reload_id=51" in err
    ), err

    # A tick while the tail is still inside the element switches nothing.
    assert engine._resume_rebase_drain(pending) is True
    assert "video_sel.set_property:active-pad=new-video" not in recorder.calls, recorder.calls

    # The window flushes: the tail crosses the mux pad and the switch proceeds.
    engine._mux_pad_airing_rt["sink_66"] = 404_181_333_332
    assert engine._resume_rebase_drain(pending) is False
    assert "video_sel.set_property:active-pad=new-video" in recorder.calls, recorder.calls


def test_rc61_a_graph_without_loudness_latency_does_not_wait_for_a_tail(
    engine_module: types.ModuleType,
) -> None:
    """A branch with no declared processing latency keeps today's predicate.

    ``audio_latency_ns`` is 0 for every graph without ``audioloudnorm``; nothing
    sits between the selector and the mux that can hold a tail, so an empty pad
    really is a consumed pad and the wait must not be introduced there."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, _video_pad, audio_pad = _u37_rebase_pending(recorder, txn_id=52, audio_level=0)
    engine._mux_pad_airing_rt = {"sink_66": 0}
    engine.output_audio_latency_ns = 0

    assert (
        engine._rebase_drain_held(
            {
                "txn_id": 52,
                "old_audio_pad": pending.get("old_audio_pad"),
                "outgoing_end": {},
                "rebase_observer_pads": [("audio", audio_pad, "sink_66")],
            }
        )
        == []
    )


def test_u37_an_unreadable_pad_level_is_not_proven_empty(engine_module: types.ModuleType) -> None:
    """Treating an unreadable level as empty would reinstate the very re-dating
    this guard exists to remove, so it waits and lets the deadline decide."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pad = _UnreadableDrainPad("sink_66", "audio/mpeg", 0, recorder)

    assert engine._rebase_drain_held(
        {"txn_id": 7, "rebase_observer_pads": [("audio", pad, "sink_66")]}
    ) == ["sink_66=unknown"]


def test_u37_without_a_timer_the_switch_is_unobserved_not_held(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """No main loop to poll on must not mean "hold the stream off air forever".

    This file's fake GLib has no ``timeout_add`` at all -- exactly the
    degradation path: the observer deadline cannot be armed, so the drain wait
    never starts and the commit proceeds synchronously as it did before U37,
    with a WARN saying the pads went unguarded. It is also why every other test
    in this module still sees an immediate switch."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, video_pad, audio_pad = _u37_rebase_pending(recorder, txn_id=43)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._pending_reload = pending

    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)

    assert "video_sel.set_property:active-pad=new-video" in recorder.calls, recorder.calls
    err = capsys.readouterr().err
    assert (
        "WARN: rebase arrival observer unavailable for reload 43: no observer "
        "deadline timer (AttributeError("
    ) in err
    assert "stage=rebase-drain-wait" not in err
    # The armer returned before it could record anything: no pad is claimed as
    # observed, so no release path can later try to remove a probe that was
    # never installed.
    assert "rebase_observer_pads" not in pending
    assert "rebase_observer_probes" not in pending


def test_u37_arrival_observer_counts_and_never_drops(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """An arrival before the rebased segment is legitimate outgoing tail.

    Measured cost of dropping it instead: 48 audio arrivals and a 1.024s hole
    in the aired audio. So the observer counts, returns OK, and removes itself
    only on the segment that closes its window -- REMOVE, never DROP, because
    the segment itself must reach the mux."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending: dict[str, Any] = {"txn_id": 7, "rebase_observer_arrived": {}}
    observe = engine._make_rebase_segment_observer(pending, "sink_66", "audio")
    ok = engine_module.Gst.PadProbeReturn.OK

    returns = [
        observe(None, _FakePadProbeInfo(engine_module.Gst.PadProbeType.BUFFER)),
        observe(None, _FakePadProbeInfo(engine_module.Gst.PadProbeType.BUFFER_LIST)),
    ]
    assert returns == [ok, ok], returns
    assert pending["rebase_observer_arrived"] == {"sink_66": 2}

    returns.append(
        observe(
            None,
            _FakePadProbeInfo(
                engine_module.Gst.PadProbeType.EVENT_DOWNSTREAM,
                _FakeSegmentEvent(engine_module.Gst.EventType.SEGMENT),
            ),
        )
    )
    assert returns[-1] == engine_module.Gst.PadProbeReturn.REMOVE, returns
    assert pending["rebase_observer_removed"] == {"sink_66"}
    assert (
        "CTRL reload diagnostic: stage=rebase-observer-disarmed pad=sink_66 "
        "stream=audio arrived=2 reload_id=7"
    ) in capsys.readouterr().err

    # A callback that raises is a per-buffer stderr flood on the streaming
    # thread: the failure mode must be "stops counting", never "takes the mux's
    # streaming thread with it".
    returns.append(observe(None, _RaisingProbeInfo()))
    returns.append(observe(None, None))
    assert returns[3:] == [ok, ok], returns
    assert engine_module.Gst.PadProbeReturn.DROP not in returns, returns


def test_u37_observer_release_skips_a_probe_that_removed_itself(
    engine_module: types.ModuleType,
) -> None:
    """Removing a self-removed probe id again is a GStreamer-WARNING on the
    stderr of every clean switch; the pad name it reported is the record."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video_pad = _FakeDrainPad("sink_65", "video/x-h264", 0, recorder)
    audio_pad = _FakeDrainPad("sink_66", "audio/mpeg", 0, recorder)
    pending: dict[str, Any] = {
        "txn_id": 7,
        "rebase_observer_pads": [
            ("video", video_pad, "sink_65"),
            ("audio", audio_pad, "sink_66"),
        ],
        "rebase_observer_probes": [
            (video_pad, "v-probe", "sink_65"),
            (audio_pad, "a-probe", "sink_66"),
        ],
        "rebase_observer_removed": {"sink_65"},
        "rebase_observer_deadline_id": 11,
        "rebase_drain_timeout_id": 12,
    }

    engine._release_rebase_observers(pending)

    assert video_pad.removed == [], video_pad.removed
    assert audio_pad.removed == ["a-probe"], audio_pad.removed
    assert pending["rebase_observer_probes"] == []
    assert pending["rebase_observer_deadline_id"] is None
    assert pending["rebase_drain_timeout_id"] is None

    # Idempotent: a second release is a no-op, not a second removal.
    engine._release_rebase_observers(pending)
    assert audio_pad.removed == ["a-probe"], audio_pad.removed


def test_u37_observer_window_close_reports_what_it_counted(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bound reports itself, naming each pad and its count: nothing is held
    off air by an observer whose segment never came."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video_pad = _FakeDrainPad("sink_65", "video/x-h264", 0, recorder)
    audio_pad = _FakeDrainPad("sink_66", "audio/mpeg", 0, recorder)
    pending: dict[str, Any] = {
        "txn_id": 7,
        "rebase_observer_pads": [
            ("video", video_pad, "sink_65"),
            ("audio", audio_pad, "sink_66"),
        ],
        "rebase_observer_probes": [
            (video_pad, "v-probe", "sink_65"),
            (audio_pad, "a-probe", "sink_66"),
        ],
        "rebase_observer_arrived": {"sink_65": 3},
        "rebase_observer_deadline_id": 11,
    }

    assert engine._on_rebase_observer_deadline(pending) is False

    assert (
        "WARN: rebase arrival observer window closed after 4.0s for reload 7: "
        "no rebased segment crossed the mux sink pads "
        "(video:sink_65=3, audio:sink_66=0); removing the observers -- nothing is "
        "held off air"
    ) in capsys.readouterr().err
    assert video_pad.removed == ["v-probe"], video_pad.removed
    assert audio_pad.removed == ["a-probe"], audio_pad.removed

    # Already disarmed: a second tick is silent.
    assert engine._on_rebase_observer_deadline(pending) is False
    assert capsys.readouterr().err == ""


# --- (9) U62: the fallback's switch point, re-read from the FURTHEST pad -------
#
# U62's live shape (C12, 2026-09-28): a slate->program ``mode=immediate``
# fallback whose commit the U37 rebase drain deferred for its whole 3.0s
# deadline. The arm-time switch point is 185.808s; by the time the commit ran the
# mux's own position was 188.7698s -- so every buffer the new leg delivered
# entered the mux ~3s in the past, the emitted PES carried the stale PTS against
# a DTS at the mux's real position (232 relay ``Invalid timestamps`` = 89 video +
# 143 audio, of which 142 are the back-dated run and one is a pre-existing AAC
# discontinuity at 169.771s), and the relay repaired the two streams
# ASYMMETRICALLY: published picture lost a single 0.033333s frame at the switch
# (seg092 video ends 188.369789, seg093 begins 188.403122) while published audio
# resumed only at 190.179s -- a 3.008s hole in the programme's sound. Measured across seg085-seg110, sync is RESTORED once the hole ends
# (seg094: video 190.169800 vs audio 190.179133; seg110: both 222.243133, the
# same ~10ms baseline as the healthy seg085/088/090), so the harm is the hole
# itself, not a lasting offset.
#
# R8's clamp already re-read the switch point from the mux's aired frontier, but
# AUDIO only -- and the audio pad is the one ``_arm_mux_tail_cutoff`` fences: a
# fenced buffer is DROPPED and a dropped buffer is never consumed, so the audio
# frontier passes the arm-time bound only once that fence has RELEASED. On the
# C12 shape it never did (no ``stage=mux-tail-released*`` line, and the drain
# burned its full 3.0s with the video pad still holding 2 buffers), so the clamp
# stayed silent -- the run that needed it is the run it could not serve. It is not
# unsatisfiable in general: at U56g's r8 probe run the fence carried a marker
# target, the drain drained (1.421s), the audio frontier ran on, and the audio-only
# clamp DID fire -- and, moving audio alone, left the two streams 1.055s apart at
# the selector (video running_time=7.094, audio running_time=8.149). The pad whose
# frontier carries the answer without waiting on that fence (video) is exactly the
# one r8 excluded, so an audio-only read fails silent-when-held and an audio-only
# write fails desynchronised-when-released. These tests pin the generalisation: the
# MAXIMUM frontier across the affected pads, applied to all of the new leg's source
# pads, with the retiring leg's cut left where it ended.

_U62_ARM_NS = 185_808_000_000
_U62_VIDEO_FRONTIER_NS = 188_769_800_000


def _u62_fallback_pending(
    recorder: _Recorder,
    *,
    txn_id: int = 44,
    video_level: int = 2,
    audio_level: int = 0,
) -> tuple[dict[str, Any], _FakeDrainPad, _FakeDrainPad]:
    """The C12 fallback shape: an arm-time switch point, a drain that never
    empties, and mux sink pads whose aired frontiers a test owns.

    The levels are C12's own: the AUDIO pad was empty by the deadline (its
    arrivals were being dropped by the arm-time fence) while the VIDEO pad still
    held two queued buffers -- which is why the live WARN names ``sink_65`` and
    not ``sink_66``."""
    pending, video, audio = _u37_rebase_pending(recorder, txn_id=txn_id, audio_level=audio_level)
    video.level = video_level
    return pending, video, audio


def _u62_deferred_commit(
    engine: Any,
    engine_module: types.ModuleType,
    pending: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arm, defer, then expire the drain deadline the way the main loop would."""
    _u37_timeout_adds(monkeypatch, engine_module)
    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)
    pending["rebase_drain_started_t"] = time.monotonic() - 3.5
    engine._resume_rebase_drain(pending)


def _last_set_offset(calls: list[str], pad_name: str) -> str:
    applied = [call for call in calls if call.startswith(f"set_offset:{pad_name}:")]
    assert applied, f"no set_offset for {pad_name}; calls={calls}"
    return applied[-1]


def test_u62_the_fallback_switch_point_is_reclamped_from_the_furthest_pad(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The C12 red, in the harness: the video pad's frontier is the answer.

    Both new-leg pads take the ONE furthest aired frontier, the retiring leg's
    cut stays where it ended, and the offsets land before the selector mutation
    releases the holds -- so nothing crosses with the stale arm-time value."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.pipeline = _FakeClockPipeline(recorder, clock_time_ns=_U62_ARM_NS)
    pending, video_pad, audio_pad = _u62_fallback_pending(recorder)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._mux_pad_airing_rt = {
        "sink_65": _U62_VIDEO_FRONTIER_NS,
        "sink_66": _U62_ARM_NS - 108_000_000,
    }
    engine._pending_reload = pending

    _u62_deferred_commit(engine, engine_module, pending, monkeypatch)

    calls = recorder.calls
    for pad_name in ("new-video", "new-audio"):
        assert _last_set_offset(calls, pad_name) == (
            f"set_offset:{pad_name}:{_U62_VIDEO_FRONTIER_NS}"
        ), calls
    assert _index_of(calls, f"set_offset:new-video:{_U62_VIDEO_FRONTIER_NS}") < _index_of(
        calls, "video_sel.set_property:active-pad=new-video"
    ), calls

    assert pending["fallback_switch_reclamped_ns"] == _U62_VIDEO_FRONTIER_NS
    # The cut is NOT moved with the offsets: the retiring leg ended at the
    # arm-time bound, and raising it here would let the already-dropped retiring
    # buffers back through the fence to be re-dated a second time.
    assert pending["old_tail_cutoff_ns"] == _U62_ARM_NS

    assert (
        "CTRL reload diagnostic: stage=fallback-switch-point-reclamped "
        f"streams=video,audio was={_U62_ARM_NS} now={_U62_VIDEO_FRONTIER_NS} "
        "pads=sink_65,sink_66 reload_id=44"
    ) in capsys.readouterr().err


def test_u62_a_measured_switch_is_never_reclamped(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The seamless/measured path is provably untouched.

    ``fallback_switch_running_time_ns`` is written by the fallback branch alone,
    so a switch with real per-stream ends keeps the ONE number its own ends
    produced -- even with frontiers far ahead of it available."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, _video_src, _audio_src = _u16_pending(recorder, txn_id=45)
    video_pad = _FakeDrainPad("sink_65", "video/x-h264", 0, recorder)
    audio_pad = _FakeDrainPad("sink_66", "audio/mpeg", 0, recorder)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._mux_pad_airing_rt = {"sink_65": 9_000_000_000, "sink_66": 9_000_000_000}
    engine._pending_reload = pending
    _u37_timeout_adds(monkeypatch, engine_module)

    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)

    assert "fallback_switch_running_time_ns" not in pending
    assert "fallback_switch_reclamped_ns" not in pending
    assert "stage=fallback-switch-point-reclamped" not in capsys.readouterr().err
    applied = [call for call in recorder.calls if call.startswith("set_offset:new-")]
    assert applied == [
        "set_offset:new-video-src:1100000000",
        "set_offset:new-audio-src:1100000000",
    ], applied


def test_u62_an_audio_only_frontier_ahead_still_fires_and_carries_both_streams(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """R8's own case, kept and made whole.

    The audio pad ahead of the frozen bound alone must still clamp -- that is
    r8's guarantee -- and it now carries the one-offset-for-both-streams
    invariant instead of leaving video on the stale value."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.pipeline = _FakeClockPipeline(recorder, clock_time_ns=_U62_ARM_NS)
    pending, video_pad, audio_pad = _u62_fallback_pending(recorder, txn_id=48)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._mux_pad_airing_rt = {
        "sink_65": _U62_ARM_NS - 8_000_000,
        "sink_66": 187_000_000_000,
    }
    engine._pending_reload = pending

    _u62_deferred_commit(engine, engine_module, pending, monkeypatch)

    calls = recorder.calls
    for pad_name in ("new-video", "new-audio"):
        assert _last_set_offset(calls, pad_name) == f"set_offset:{pad_name}:187000000000"
    assert (
        "CTRL reload diagnostic: stage=fallback-switch-point-reclamped "
        f"streams=video,audio was={_U62_ARM_NS} now=187000000000 "
        "pads=sink_65,sink_66 reload_id=48"
    ) in capsys.readouterr().err


def test_u62_a_frontier_behind_the_switch_point_is_not_a_clamp(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """The offset stands when nothing has aired past it: no line, no mutation."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video_pad = _FakeDrainPad("sink_65", "video/x-h264", 0, recorder)
    audio_pad = _FakeDrainPad("sink_66", "audio/mpeg", 0, recorder)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._mux_pad_airing_rt = {
        "sink_65": _U62_ARM_NS - 8_000_000,
        "sink_66": _U62_ARM_NS,
    }
    video_src = _FakeHoldPad("new-video", recorder)
    audio_src = _FakeHoldPad("new-audio", recorder)
    pending: dict[str, Any] = {
        "txn_id": 46,
        "fallback_switch_running_time_ns": _U62_ARM_NS,
        "new_video_pad": object(),
        "new_audio_pad": object(),
        "new_src_pads": [video_src, audio_src],
    }

    engine._clamp_fallback_switch_point(pending)

    assert capsys.readouterr().err == ""
    assert "fallback_switch_reclamped_ns" not in pending
    assert recorder.calls == [], recorder.calls


def test_u62_a_new_leg_pad_without_set_offset_is_skipped_not_fatal(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pad that cannot be re-offset must not take the commit with it, and the
    line names only the streams it actually moved."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    video_pad = _FakeDrainPad("sink_65", "video/x-h264", 0, recorder)
    audio_pad = _FakeDrainPad("sink_66", "audio/mpeg", 0, recorder)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._mux_pad_airing_rt = {
        "sink_65": _U62_ARM_NS + 1_000_000_000,
        "sink_66": _U62_ARM_NS + 1_000_000_000,
    }
    opaque = types.SimpleNamespace(name="new-video")
    audio_src = _FakeHoldPad("new-audio", recorder)
    pending: dict[str, Any] = {
        "txn_id": 47,
        "fallback_switch_running_time_ns": _U62_ARM_NS,
        "new_video_pad": object(),
        "new_audio_pad": object(),
        "new_src_pads": [opaque, audio_src],
    }

    engine._clamp_fallback_switch_point(pending)

    assert recorder.calls == ["set_offset:new-audio:186808000000"], recorder.calls
    assert pending["fallback_switch_reclamped_ns"] == _U62_ARM_NS + 1_000_000_000
    assert (
        "CTRL reload diagnostic: stage=fallback-switch-point-reclamped "
        f"streams=audio was={_U62_ARM_NS} now=186808000000 "
        "pads=sink_65,sink_66 reload_id=47"
    ) in capsys.readouterr().err


# --- U62 round 2: the fallback's mux fence had no bound, so it never held -----
#
# Round 1 (above) cut the published slate->program gap from 3.008 s to 0.085 s on
# C13. The residue is one mechanism, measured on the fixture
# (``fixtures/C13-public-0831``, segs 100-118 + the relay's stderr): the retiring
# slate leg's audio tail crossed the switch point at the mux, so the mux's own
# clock stood ahead of the new leg's first buffers when they arrived; the relay
# then reported seven ``Invalid timestamps`` and repaired each one, and that
# repair is what re-dates the new leg's first video frames onto the outgoing
# frontier (221.835944 / .835956, three frames inside 12 ms).
#
# The engine's own evidence for WHY the tail crossed is in the worker log of the
# same C13 event: ``stage=mux-tail-armed pads=sink_66 target=None``. A fence armed
# with no target is vacuous -- ``_mux_tail_arrived`` returns True for an empty
# target map -- so ``_defer_mux_tail_release`` disarmed it at the first selector
# mutation (neither ``stage=mux-tail-wait`` nor ``stage=mux-tail-released``
# appears in that window), and the retiring audio's post-bound arrivals were never
# dropped. The fallback is exactly the case with no measured per-stream end to
# seed a target from: its outgoing leg is an endless slate that never EOSes.
#
# The switch point IS the bound the retiring leg is cut at, so it is also the
# value its tail must reach before the fence may come off -- the same number the
# selector-side fence and the new leg's rebase offsets already share, read by one
# more reader. These tests pin that, and pin that the measured path still targets
# its OWN measured end.

_U62B_MEASURED_AUDIO_END_NS = 1_100_000_000


def _u62b_armed_fallback_fence(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    txn_id: int = 61,
) -> tuple[Any, dict[str, Any], _FakeDrainPad, _FakeDrainPad]:
    """Drive a fallback commit through the real path and hand back the fence.

    The pads' aired frontiers are left exactly ON the arm-time bound, so the
    round-1 reclamp has nothing to correct and the switch point this test reads
    is the unmodified one -- the C13 shape.
    """
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    engine.pipeline = _FakeClockPipeline(recorder, clock_time_ns=_U62_ARM_NS)
    pending, video_pad, audio_pad = _u62_fallback_pending(recorder, txn_id=txn_id)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._mux_pad_airing_rt = {"sink_65": _U62_ARM_NS, "sink_66": _U62_ARM_NS}
    engine._pending_reload = pending

    _u62_deferred_commit(engine, engine_module, pending, monkeypatch)
    return engine, pending, video_pad, audio_pad


def test_u62b_the_fallback_fence_targets_the_switch_point(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """RED at the C13 base: ``target=None`` -- a fence armed with no bound at all.

    Without one there is nothing for the fence to wait for, which is why the
    live window shows no wait and no release."""
    engine, pending, _video_pad, audio_pad = _u62b_armed_fallback_fence(engine_module, monkeypatch)
    err = capsys.readouterr().err

    assert "fallback_switch_reclamped_ns" not in pending
    assert pending["old_tail_cutoff_ns"] == _U62_ARM_NS, pending["old_tail_cutoff_ns"]
    assert pending.get("mux_tail_target") == {"sink_66": pending["old_tail_cutoff_ns"]}, (
        pending.get("mux_tail_target")
    )
    assert (
        f"CTRL reload diagnostic: stage=mux-tail-armed pads=sink_66 "
        f"target={{'sink_66': {pending['old_tail_cutoff_ns']}}} "
        f"cutoff={pending['old_tail_cutoff_ns']} reload_id=61"
    ) in err
    # The fence is still on the AUDIO pad only (U56 round 5's measurement: a
    # video fence here drops the incoming leg's own first picture). The later
    # ``_observe_rebase_arrivals`` on the same pad is U37's drain observer, not
    # this fence -- hence the prefix read, not an equality.
    assert [callback.__name__ for _id, _mask, callback in audio_pad.probes][:2] == [
        "_mux_tail_cutoff_probe",
        "_mux_tail_segment_probe",
    ], audio_pad.probes
    assert not [
        callback.__name__
        for _id, _mask, callback in engine._mux_input_pads["sink_65"].probes
        if callback.__name__.startswith("_mux_tail")
    ], engine._mux_input_pads["sink_65"].probes


def test_u62b_the_fallback_fence_holds_instead_of_releasing_at_the_mutation(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """RED at the C13 base: the fence came off at the first selector mutation.

    That is the whole residue in one assertion -- ``_defer_mux_tail_release``
    found an empty target map, ``_mux_tail_arrived`` said yes, and the retiring
    audio's arrivals past the bound were never dropped."""
    engine, pending, _video_pad, _audio_pad = _u62b_armed_fallback_fence(
        engine_module, monkeypatch, txn_id=62
    )
    err = capsys.readouterr().err

    assert pending.get("mux_tail_released") is not True, "the fence must still hold"
    assert (
        "CTRL reload diagnostic: stage=mux-tail-wait "
        f"seen=None target={{'sink_66': {pending['old_tail_cutoff_ns']}}} reload_id=62"
    ) in err
    assert "stage=mux-tail-released" not in err
    # Held by a bounded poller, not by blocking the main loop.
    assert engine._pending_reload is pending


def test_u62b_the_fallback_fence_still_releases_on_the_measure(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The new bound is reachable: it comes off on the retiring tail's arrival.

    A guard on both revisions -- the empty-target fence released immediately,
    so it passed the same assertion for the wrong reason. It is here to prove
    the target added above can actually be satisfied, i.e. that the fence is a
    wait and not a permanent hold."""
    engine, pending, _video_pad, _audio_pad = _u62b_armed_fallback_fence(
        engine_module, monkeypatch, txn_id=63
    )
    capsys.readouterr()
    pending["mux_tail_seen"] = {"sink_66": int(pending["old_tail_cutoff_ns"])}
    pending["mux_last_arrival_t"] = time.monotonic() - 1.0

    engine._defer_mux_tail_release(pending)
    err = capsys.readouterr().err

    assert pending["mux_tail_released"] is True
    assert "stage=mux-tail-wait" not in err
    # And the poller that was armed agrees, rather than waiting for the deadline.
    assert engine._poll_mux_tail_release(pending) is False
    released = capsys.readouterr().err
    assert "stage=mux-tail-released waited=" in released, released
    assert "arrived=True" in released, released


def test_u62b_the_measured_path_still_targets_its_own_measured_end(
    engine_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No change to the seamless path: a real per-stream end still wins.

    The switch point is the fallback's bound only because the fallback has no
    measured end to offer. Where one exists the fence keeps using it -- here a
    cutoff deliberately far from (and later than) the measured audio end must
    not displace it."""
    recorder = _Recorder()
    engine = _bare_engine_for_commit(engine_module, recorder)
    pending, _video_src, _audio_src = _u16_pending(recorder, txn_id=64)
    # Make video the longer measured leg so the common switch cutoff and the
    # audio mux fence target remain distinct values in this assertion.
    pending["outgoing_end"][pending["old_video_pad"]]["end"] = 1_800_000_000
    video_pad = _FakeDrainPad("sink_65", "video/x-h264", 0, recorder)
    audio_pad = _FakeDrainPad("sink_66", "audio/mpeg", 0, recorder)
    engine._mux_input_pads = {"sink_65": video_pad, "sink_66": audio_pad}
    engine._mux_pad_airing_rt = {"sink_65": 9_000_000_000, "sink_66": 9_000_000_000}
    engine._pending_reload = pending
    _u37_timeout_adds(monkeypatch, engine_module)

    engine._prepare_reload_handoff(pending)
    engine._begin_reload_commit(pending)
    err = capsys.readouterr().err

    assert pending["old_tail_cutoff_ns"] != _U62B_MEASURED_AUDIO_END_NS
    assert pending.get("mux_tail_target") == {"sink_66": _U62B_MEASURED_AUDIO_END_NS}, pending.get(
        "mux_tail_target"
    )
    assert f"target={{'sink_66': {_U62B_MEASURED_AUDIO_END_NS}}}" in err, err


# --- U63: the changeover segment may not overtake queued retiring audio -------


class _FakeLevelPad:
    """A mux sink pad whose queue level is a scripted sequence, then its last."""

    def __init__(self, name: str, levels: list[int | None], recorder: _Recorder) -> None:
        self.name = name
        self.recorder = recorder
        self.levels = list(levels)
        self.reads = 0

    def get_name(self) -> str:
        return self.name

    def get_property(self, name: str) -> Any:
        assert name == "current-level-buffers"
        self.recorder.calls.append(f"{self.name}.get_property:{name}")
        level = self.levels[min(self.reads, len(self.levels) - 1)]
        self.reads += 1
        return level


class _FakeSegmentEventInfo:
    def __init__(self, event: Any) -> None:
        self.type = _FakePadProbeType.EVENT_DOWNSTREAM
        self._event = event

    def get_event(self) -> Any:
        return self._event


def test_u63_segment_drain_waits_until_the_pad_hands_every_buffer_over(
    engine_module: types.ModuleType,
) -> None:
    """The pure wait: bounded, polling, and honest about every outcome."""
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def _now() -> float:
        return clock["t"]

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    levels = [2, 1, 0]
    reads = iter(levels)
    drained, level = engine_module._drain_mux_pad_before_segment(
        lambda: next(reads), 1.0, poll_s=0.005, now=_now, sleep=_sleep
    )
    assert (drained, level) == (True, 0)
    assert sleeps == [0.005, 0.005]

    # A pad that never empties costs the bound and reports what is left.
    clock["t"] = 0.0
    sleeps.clear()
    drained, level = engine_module._drain_mux_pad_before_segment(
        lambda: 3, 0.02, poll_s=0.005, now=_now, sleep=_sleep
    )
    assert (drained, level) == (False, 3)
    assert clock["t"] >= 0.02
    assert 1 <= len(sleeps) <= 5

    # An unreadable level proves nothing and must not be waited on.
    clock["t"] = 0.0
    sleeps.clear()
    assert engine_module._drain_mux_pad_before_segment(
        lambda: None, 1.0, poll_s=0.005, now=_now, sleep=_sleep
    ) == (False, None)
    assert sleeps == []


def test_u63_changeover_segment_is_held_until_the_audio_pad_empties(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """The probe that actually stops the re-dating.

    A queued retiring buffer is clipped at POP time under the segment current
    THEN. Blocking the changeover segment on the pad until its queue empties is
    what makes that impossible; the diagnostic must say the wait happened.
    """
    recorder = _Recorder()
    pad = _FakeLevelPad("sink_66", [2, 1, 0], recorder)
    old_segment = types.SimpleNamespace(start=0, base=0)
    pending: dict[str, Any] = {
        "txn_id": 63,
        "outgoing_end": {pad: {"end": 1_000, "segment": old_segment}},
        "mux_segment_drain_bound_s": 1.0,
    }
    incoming = types.SimpleNamespace(start=900_000_000, base=900_000_000)
    info = _FakeSegmentEventInfo(
        types.SimpleNamespace(type=_FakeEventType.SEGMENT, parse_segment=lambda: incoming)
    )

    assert engine_module._mux_tail_segment_probe(pad, info, pending) == _FakePadProbeReturn.OK
    err = capsys.readouterr().err
    assert "stage=mux-segment-drained pad=sink_66" in err, err
    assert pad.reads == 3, pad.reads
    assert pending["mux_tail_segment_crossed"] == {pad}
    assert pending["mux_tail_released"] is True
    assert "stage=mux-tail-released-by-segment" in err, err

    # A re-sent sticky segment is not a new origin: no second wait.
    pad.reads = 0
    assert engine_module._mux_tail_segment_probe(pad, info, pending) == _FakePadProbeReturn.OK
    assert pad.reads == 0


def test_u63_changeover_segment_still_crosses_when_the_pad_never_empties(
    engine_module: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bounded wait may NOT wedge the channel: it reports and passes the event."""
    recorder = _Recorder()
    pad = _FakeLevelPad("sink_66", [4], recorder)
    pending: dict[str, Any] = {
        "txn_id": 64,
        "outgoing_end": {pad: {"end": 1_000, "segment": types.SimpleNamespace(start=0, base=0)}},
        "mux_segment_drain_bound_s": 0.01,
    }
    info = _FakeSegmentEventInfo(
        types.SimpleNamespace(
            type=_FakeEventType.SEGMENT,
            parse_segment=lambda: types.SimpleNamespace(start=5, base=5),
        )
    )
    assert engine_module._mux_tail_segment_probe(pad, info, pending) == _FakePadProbeReturn.OK
    err = capsys.readouterr().err
    assert "retiring audio buffer(s) still queued on mux pad sink_66" in err, err
    assert "audio PTS step back" in err, err
