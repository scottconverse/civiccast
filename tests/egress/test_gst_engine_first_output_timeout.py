# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Item 84 (measured in sandbox run 15, soak-fcfcb81-20260906-183448Z, and in
three seamless-OFF runs): every fresh worker printed
``CTRL preroll: reached PLAYING after 0.3s`` (a real, fast PLAYING transition)
immediately followed by ``CTRL stall: no output for 10s - quitting for daemon
restart`` -- ``GstPlayoutEngine._await_playing`` accepts NO_PREROLL as
success, so PLAYING is not evidence a single buffer crossed the mux, and
``_arm_stall_watchdog`` armed the 10s post-first-buffer stall bound
(``stall_timeout_s``) the instant PLAYING was reached. Under start-up load (a
concurrent ffmpeg conform, a ~10s synchronous content-reload source
preparation on the automation thread, live caption-tap overload) the first
output buffer can legitimately take longer than 10s, killing a perfectly
healthy worker.

This file covers the engine-side fix: ``_check_stall`` now measures two
DISTINCT budgets -- ``first_output_timeout_s`` (45s default) while no output
has been observed yet, and the original ``stall_timeout_s`` (10s default,
unchanged behavior) once the first buffer IS observed -- plus
``_resolve_first_output_timeout_s``'s default/env/clamp/NaN-guard resolution,
mirroring ``tests/egress/test_gst_engine_preroll_timeout.py`` exactly (same
fake-``gi``/``Gst`` fixture, no real GStreamer install needed).
"""

from __future__ import annotations

import importlib
import math
import sys
import types
from typing import Any

import pytest

_ENGINE_MODULE_NAME = "civiccast.egress.gst.engine"


@pytest.fixture
def engine_module():
    """Load ``civiccast.egress.gst.engine`` fresh against a fake ``gi``/``Gst``
    that needs no real GStreamer install -- mirrors the fixture in
    ``test_gst_engine_preroll_timeout.py`` and
    ``test_gst_engine_reload_concat_naming.py``."""
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_repository = types.ModuleType("gi.repository")
    fake_glib = types.ModuleType("gi.repository.GLib")
    fake_glib.timeout_add_seconds = lambda *_a, **_k: None  # type: ignore[attr-defined]
    fake_gst = types.ModuleType("gi.repository.Gst")
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


def _bare_engine(
    module: types.ModuleType,
    *,
    first_output_timeout_s: float,
    stall_timeout_s: float,
) -> Any:
    """A ``GstPlayoutEngine`` with none of ``__init__``'s heavy pipeline-building
    run -- just the attributes ``_check_stall`` actually touches."""
    engine = object.__new__(module.GstPlayoutEngine)
    engine.first_output_timeout_s = first_output_timeout_s
    engine.stall_timeout_s = stall_timeout_s
    engine._first_output_seen = False
    engine._output_buffers = 0
    engine._stall_last_count = 0
    engine._stall_last_advance_t = 0.0
    engine._error = None
    engine._loop = None
    # Item 84 Round-2 review BLOCKER additions -- ``_check_stall`` /
    # ``_arm_stall_watchdog`` now also touch these.
    engine._playing_reached_at = None
    engine._first_output_marker_printed = False
    # Item 84c additions -- the arm-time snapshot the "real first output"
    # check is measured against, and the progress-line throttle state.
    engine._output_buffers_at_arm = 0
    engine._last_output_progress_print_t = 0.0
    # Round-2 finding 2 additions -- the post-first-output stall bound now yields
    # its window to the commit watchdog while a reload commit is in flight, so
    # ``_check_stall`` reads the pending transaction and the commit bound too.
    engine._pending_reload = None
    engine.commit_timeout_s = 15.0
    # U30 additions -- the progress line now also carries each stream's OWN mux
    # sink-pad delta, and ``_arm_stall_watchdog`` snapshots that counter set at
    # arm time exactly as it does for ``_output_buffers``.
    engine._mux_input_pads = {}
    engine._mux_input_buffers = {}
    engine._mux_input_snapshot = {}
    engine._mux_input_snapshot_t = 0.0
    # U37 additions -- the per-stream watchdog now judges each mux SINK pad by
    # its AIRING running time (``buffer-consumed``), not by its arrival count:
    # a stream can keep arriving at its normal rate while the mux emits none of
    # it, which an arrival count reads as healthy. Tests that drive the judge
    # feed this map; the ``mux-in`` progress line still reads the counters above.
    engine._mux_pad_airing_rt = {}
    return engine


class _FakeLoop:
    def __init__(self) -> None:
        self.quit_calls = 0

    def quit(self) -> None:
        self.quit_calls += 1


# --- _check_stall: the two-budget split ---------------------------------------------


def test_check_stall_never_trips_while_output_is_advancing(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()

    for i in range(1, 6):
        engine._output_buffers = i
        clock["t"] += 1.0
        assert engine._check_stall() is True
    assert engine._error is None
    assert engine._loop.quit_calls == 0
    assert engine._first_output_seen is True


def test_check_stall_fires_the_first_output_timeout_before_any_buffer_is_seen(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The item 84 fix's own core case: PLAYING was reached (armed at t=0) but
    NO buffer has ever crossed the mux -- once ``first_output_timeout_s``
    elapses, the engine quits with the distinct ``first-output-timeout``
    reason, never the ordinary ``stall_timeout_s`` (10s) bound, which would
    have tripped this scenario immediately under the OLD code."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()

    # Ticks well past the OLD 10s stall bound, but still under the NEW 45s
    # first-output bound -- must NOT trip yet (the exact scenario the old
    # code got wrong).
    for _ in range(20):
        clock["t"] += 1.0
        assert engine._check_stall() is True
    assert engine._error is None

    # Past the 45s first-output bound: now it trips.
    clock["t"] = 46.0
    result = engine._check_stall()

    assert result is False
    assert engine._error == ("first-output-timeout", "no output buffers observed within bound")
    assert engine._loop.quit_calls == 1
    err = capsys.readouterr().err
    assert "CTRL first-output: no output within 45s of PLAYING" in err


def test_check_stall_still_applies_the_ordinary_stall_bound_after_first_output(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Once the first buffer IS observed, the original S9-5 behavior applies
    completely unchanged: a 10s gap with no further advance trips the
    ordinary ``("stall", ...)`` reason, never the first-output-TIMEOUT one.
    The positive-evidence ``CTRL first-output: first buffer after ...``
    marker (item 84 Round-2) DOES print once, at the moment the first
    buffer is observed -- that is expected and desired (it is the daemon's
    on-air evidence for this exact scenario), distinct from the
    first-output-timeout FAILURE line this test proves never fires here."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()

    # Item 84c: real first output requires the count to exceed the arm-time
    # snapshot (0 here) by at least ``_FIRST_OUTPUT_MIN_BUFFERS_AFTER_ARM``
    # (2) -- one buffer alone could still be a table-refresh coincidence.
    engine._output_buffers = 2  # two real buffers observed post-arm at t=0
    assert engine._check_stall() is True
    assert engine._first_output_seen is True

    clock["t"] += 9.0
    assert engine._check_stall() is True  # still inside the 10s stall bound
    assert engine._error is None

    clock["t"] += 2.0  # 11s since the last advance
    result = engine._check_stall()

    assert result is False
    assert engine._error == ("stall", "output stalled")
    assert engine._loop.quit_calls == 1
    err = capsys.readouterr().err
    assert "CTRL stall: no output for 10s" in err
    assert "CTRL first-output: no output within" not in err  # the TIMEOUT line, never printed
    assert "CTRL first-output: first buffer after" in err  # the SUCCESS marker, printed once


def test_check_stall_forces_a_ready_reload_when_outgoing_eos_is_missing(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ready deferred replacement must recover a frozen outgoing leg instead
    of exiting the worker before the existing forced-switch timeout can fire."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._first_output_seen = True
    engine._output_buffers = 2
    engine._stall_last_count = 2
    engine._stall_last_advance_t = 0.0
    engine._pending_reload = {
        "txn_id": 7,
        "switch_at_end_of_current": True,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "old_leg_eos": False,
        "commit_in_progress": False,
    }
    commits: list[bool] = []
    engine._commit_reload = lambda: commits.append(True)  # type: ignore[method-assign]

    clock["t"] = 11.0
    assert engine._check_stall() is True
    assert commits == [True]
    assert engine._pending_reload["boundary_forced"] is True
    assert (
        "output stalled after replacement preroll; forcing switch (reload_id=7)"
        in capsys.readouterr().out
    )
    assert engine._error is None


def test_check_stall_does_not_suppress_stall_when_reload_holds_remain(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()
    engine._first_output_seen = True
    engine._output_buffers = 2
    engine._stall_last_count = 2
    engine._pending_reload = {
        "txn_id": 7,
        "switch_at_end_of_current": True,
        "new_leg_ready": True,
        "holds_awaited": 1,
        "old_leg_eos": False,
        "boundary_forced": False,
        "commit_in_progress": False,
    }

    clock["t"] = 11.0
    assert engine._check_stall() is False
    assert engine._error == ("stall", "output stalled")
    assert engine._loop.quit_calls == 1


@pytest.mark.parametrize("field", ["holds_awaited", "old_leg_eos", "boundary_forced"])
def test_force_deferred_boundary_rejects_non_forceable_reload(engine_module, field: str) -> None:
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._pending_reload = {
        "txn_id": 7,
        "switch_at_end_of_current": True,
        "new_leg_ready": True,
        "holds_awaited": 0,
        "old_leg_eos": False,
        "boundary_forced": False,
        "commit_in_progress": False,
    }
    engine._pending_reload[field] = {
        "holds_awaited": 1,
        "old_leg_eos": True,
        "boundary_forced": True,
    }[field]
    commits: list[bool] = []
    engine._commit_reload = lambda: commits.append(True)  # type: ignore[method-assign]

    assert engine._force_deferred_boundary(7, reason="test") is False
    assert commits == []
    assert engine._pending_reload["boundary_forced"] is (field == "boundary_forced")


def test_check_stall_ordinary_stall_disabled_at_zero_after_first_output(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The post-first-buffer stall check keeps its pre-item-84 opt-out
    semantics (``stall_timeout_s <= 0`` disables just that check) even though
    ``_arm_stall_watchdog`` now arms unconditionally whenever EITHER budget is
    active."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=0.0)
    engine._loop = _FakeLoop()

    # Item 84c: two buffers past the arm-time snapshot (0), not one -- see
    # ``_FIRST_OUTPUT_MIN_BUFFERS_AFTER_ARM``.
    engine._output_buffers = 2
    assert engine._check_stall() is True
    assert engine._first_output_seen is True

    clock["t"] = 10_000.0
    assert engine._check_stall() is True
    assert engine._error is None
    assert engine._loop.quit_calls == 0


# --- _maybe_print_first_output_marker: the positive on-air evidence marker ----------
#
# Item 84 Round-2 review BLOCKER: ``EgressDaemon._observed_on_air_evidence``
# crediting the ``CTRL preroll: reached PLAYING`` marker alone as GStreamer
# on-air evidence let a worker that reaches PLAYING on every relaunch, but
# never actually produces output, get its crash-loop streak reset every
# alive-poll cycle -- measured: streak pinned at 1, never escalating to
# fallback slate, at EVERY tested ``first_output_timeout_s`` from 65s through
# the 120s clamp ceiling. The fix is this NEW, separate, positive-evidence
# marker, printed exactly once the moment a real buffer crosses the mux --
# these tests prove the marker itself (the daemon-side escalation tests in
# ``test_daemon_first_output_timeout_relaunch.py`` prove the consuming half).


def test_first_output_marker_prints_once_when_output_first_advances(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = {"t": 5.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.os, "getpid", lambda: 4242)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()
    engine._playing_reached_at = 5.0  # PLAYING was reached at this same instant

    # Item 84c: two buffers past the arm-time snapshot (0, from _bare_engine)
    # -- one alone would not yet count as real first output.
    engine._output_buffers = 2
    clock["t"] = 8.5
    assert engine._check_stall() is True

    err = capsys.readouterr().err
    assert "CTRL first-output: first buffer after 3.5s pid=4242" in err
    assert engine._first_output_marker_printed is True


def test_first_output_marker_never_prints_twice(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()
    engine._playing_reached_at = 0.0

    for i in range(1, 4):
        engine._output_buffers = i
        clock["t"] += 1.0
        assert engine._check_stall() is True

    err = capsys.readouterr().err
    assert err.count("CTRL first-output: first buffer after") == 1


def test_arm_stall_watchdog_snapshots_preroll_buffers_instead_of_crediting_them(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Item 84c (sandbox run 17, soak-a6d7871-20260906-213332Z): the OLD
    round-2 behavior this test used to cover -- ``_output_buffers > 0`` at
    arm time alone counting as first-output evidence -- was a TAUTOLOGY. The
    persistent output half's async sink chain means the pipeline cannot even
    reach PLAYING before at least one buffer (PAT/PMT/SDT tables + preroll)
    has already crossed the mux, so that check was true for essentially
    every worker on every arm, real media flow or not. Arming must snapshot
    the count instead of crediting it: ``_first_output_seen`` stays False and
    no marker prints at arm time, even though buffers were already flowing
    before arm."""
    clock = {"t": 10.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    monkeypatch.setattr(engine_module.os, "getpid", lambda: 777)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._playing_reached_at = 7.0  # PLAYING reached 3s before this arm call
    engine._output_buffers = 5  # preroll already produced buffers before arm

    engine._arm_stall_watchdog()

    assert engine._first_output_seen is False  # item 84c's own fix
    assert engine._output_buffers_at_arm == 5  # snapshot, not credited as evidence
    err = capsys.readouterr().err
    assert "CTRL first-output: first buffer after" not in err  # no marker at arm time


def test_first_output_marker_prints_once_real_post_arm_output_is_observed(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Continuing the arm-time snapshot scenario above: once
    ``_FIRST_OUTPUT_MIN_BUFFERS_AFTER_ARM`` (2) further buffers arrive AFTER
    arming, that IS real first-output evidence, and the marker prints
    measuring elapsed from PLAYING as before."""
    clock = {"t": 10.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    monkeypatch.setattr(engine_module.os, "getpid", lambda: 777)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._playing_reached_at = 7.0
    engine._output_buffers = 5
    engine._arm_stall_watchdog()  # snapshot at 5, no evidence yet
    engine._loop = _FakeLoop()

    clock["t"] = 13.0  # 3s after arm
    engine._output_buffers = 7  # +2 past the snapshot -- real output

    assert engine._check_stall() is True

    assert engine._first_output_seen is True
    err = capsys.readouterr().err
    assert "CTRL first-output: first buffer after 6.0s pid=777" in err


def test_first_output_marker_falls_back_to_zero_elapsed_when_playing_never_recorded(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Defensive fallback: ``_playing_reached_at`` stays ``None`` if
    ``_maybe_print_first_output_marker`` is ever reached without
    ``_await_playing`` having run (should not happen in production, but must
    never raise) -- the elapsed text degrades to 0.0s rather than crashing."""
    clock = {"t": 42.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()
    assert engine._playing_reached_at is None

    # Item 84c: two buffers past the arm-time snapshot (0, from _bare_engine).
    engine._output_buffers = 2
    engine._check_stall()

    err = capsys.readouterr().err
    assert "CTRL first-output: first buffer after 0.0s" in err


# --- _arm_stall_watchdog: arms unless BOTH budgets are disabled ----------------------
#
# Coordinator review round 2: ``first_output_timeout_s=0.0`` is an UNREACHABLE
# configuration through the real constructor -- ``_resolve_first_output_
# timeout_s`` always clamps to ``[10, 120]``, so no real ``GstPlayoutEngine``
# can ever have this attribute at 0. Two tests here used to construct a bare
# engine and set that attribute directly (bypassing the resolver entirely) to
# exercise the ``_arm_stall_watchdog``/``_check_stall`` "both budgets
# disabled" branch -- removed rather than kept, since a passing test against
# a state the product can never reach is misleading, not coverage.
# ``test_arm_stall_watchdog_still_arms_when_only_stall_timeout_is_disabled``
# below already covers the one REACHABLE disabled-budget state
# (``stall_timeout_s <= 0`` alone, which IS a real, unclamped, operator-
# settable value via ``CIVICCAST_STALL_TIMEOUT_S``).


def test_arm_stall_watchdog_still_arms_when_only_stall_timeout_is_disabled(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before item 84, ``stall_timeout_s <= 0`` disabled the watchdog
    entirely -- that silently also disabled the (independently useful)
    first-output check. Now it must still arm as long as the first-output
    budget is active."""
    calls: list[Any] = []
    monkeypatch.setattr(
        engine_module.GLib, "timeout_add_seconds", lambda *a, **k: calls.append((a, k))
    )
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=0.0)

    engine._arm_stall_watchdog()

    assert len(calls) == 1


def test_arm_stall_watchdog_resets_first_output_seen(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._first_output_seen = True

    engine._arm_stall_watchdog()

    assert engine._first_output_seen is False


# --- _resolve_first_output_timeout_s: default / env / clamp / NaN guard -------------


def test_resolve_first_output_timeout_defaults_to_45s(engine_module) -> None:
    assert engine_module._resolve_first_output_timeout_s(None) == 45.0


def test_resolve_first_output_timeout_reads_the_env_override(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S", "60")
    assert engine_module._resolve_first_output_timeout_s(None) == 60.0


def test_resolve_first_output_timeout_clamps_the_env_override_to_10s_minimum(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S", "1")
    assert engine_module._resolve_first_output_timeout_s(None) == 10.0


def test_resolve_first_output_timeout_clamps_the_env_override_to_120s_maximum(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S", "999")
    assert engine_module._resolve_first_output_timeout_s(None) == 120.0


def test_resolve_first_output_timeout_ignores_a_malformed_env_value(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S", "not-a-number")
    assert engine_module._resolve_first_output_timeout_s(None) == 45.0


def test_resolve_first_output_timeout_explicit_value_wins_over_env(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S", "60")
    assert engine_module._resolve_first_output_timeout_s(30.0) == 30.0


def test_resolve_first_output_timeout_clamps_an_explicit_value_too(engine_module) -> None:
    assert engine_module._resolve_first_output_timeout_s(1.0) == 10.0
    assert engine_module._resolve_first_output_timeout_s(999.0) == 120.0


def test_resolve_first_output_timeout_env_nan_falls_back_to_default(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors the preroll bound's own NaN-clamp-escape fix: Python's
    ``min``/``max`` do not clamp NaN (every comparison with NaN is False), so
    an unguarded NaN would reach the watchdog's elapsed-time comparisons and
    never trip either budget at all."""
    monkeypatch.setenv("CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S", "nan")
    resolved = engine_module._resolve_first_output_timeout_s(None)
    assert resolved == 45.0
    assert math.isfinite(resolved)


def test_resolve_first_output_timeout_explicit_nan_falls_back_to_default(
    engine_module,
) -> None:
    resolved = engine_module._resolve_first_output_timeout_s(float("nan"))
    assert resolved == 45.0
    assert math.isfinite(resolved)


def test_resolve_first_output_timeout_explicit_infinity_falls_back_to_default(
    engine_module,
) -> None:
    resolved = engine_module._resolve_first_output_timeout_s(float("inf"))
    assert resolved == 45.0
    assert math.isfinite(resolved)


def test_resolve_first_output_timeout_120s_is_still_the_allowed_maximum(
    engine_module,
) -> None:
    assert engine_module._resolve_first_output_timeout_s(120.0) == 120.0


def test_resolve_first_output_timeout_10s_is_still_the_allowed_minimum(
    engine_module,
) -> None:
    assert engine_module._resolve_first_output_timeout_s(10.0) == 10.0


# --- _maybe_print_output_progress: item 84c addendum, bounded 5s breadcrumb --------
#
# Sandbox run 17 (soak-a6d7871-20260906-213332Z) had no signal at all between
# "output was flowing" and the eventual watchdog kill -- the only evidence of
# the item 88 stall was TSDuck's after-the-fact silence. This line gives the
# NEXT soak exactly that signal, bounded to at most one line per 5s so it
# cannot become its own source of log spam under sustained healthy output.


def test_output_progress_line_prints_at_most_once_per_five_seconds(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._output_buffers = 100
    engine._arm_stall_watchdog()  # snapshots at 100, starts the progress clock
    engine._loop = _FakeLoop()

    for i in range(1, 5):  # t=1,2,3,4 -- under the 5s interval, must not print
        clock["t"] = float(i)
        engine._output_buffers = 100 + i
        assert engine._check_stall() is True
    assert "CTRL output:" not in capsys.readouterr().err

    clock["t"] = 5.0  # exactly at the interval -- prints now
    engine._output_buffers = 106
    assert engine._check_stall() is True
    err = capsys.readouterr().err
    assert "CTRL output: 106 buffers (+6) since PLAYING" in err


def test_output_progress_line_prints_even_while_stalled(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The breadcrumb is not gated on output advancing -- it must also print
    while the count is flat, so a soak shows the count stopped changing
    (rather than going silent) in the run-up to the eventual stall kill."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._output_buffers = 50
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()

    clock["t"] = 5.0  # no advance at all since arm -- still due a progress line
    assert engine._check_stall() is True
    err = capsys.readouterr().err
    assert "CTRL output: 50 buffers (+0) since PLAYING" in err


def test_output_progress_line_delta_is_relative_to_the_arm_time_snapshot(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._output_buffers = 1_000  # a lot of preroll/table churn already counted
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()

    clock["t"] = 5.0
    engine._output_buffers = 1_003
    assert engine._check_stall() is True
    err = capsys.readouterr().err
    assert "CTRL output: 1003 buffers (+3) since PLAYING" in err


class _CapsPadStub:
    """A mux sink pad with negotiated caps, as the label resolver sees it."""

    def __init__(self, name: str, caps: str) -> None:
        self._name = name
        self._caps = caps

    def get_name(self) -> str:
        return self._name

    def get_current_caps(self) -> Any:
        return types.SimpleNamespace(to_string=lambda: self._caps)


def test_output_progress_line_names_each_streams_mux_input_delta(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """U30: the 5s breadcrumb says WHICH stream is feeding the mux.

    Two live incidents of 2026-09-25 aired audio with no video after a deferred
    reload committed, and the total in this very line kept advancing throughout
    (at the audio-only rate, measured: 234/281 per interval against 332-496 for
    healthy A/V), so it read as healthy. The per-stream delta makes ``video=+0``
    a thing the log says out loud."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._mux_input_pads = {
        "sink_65": _CapsPadStub("sink_65", "video/x-h264, stream-format=byte-stream"),
        "sink_66": _CapsPadStub("sink_66", "audio/mpeg, mpegversion=4"),
    }
    # The arm-time baseline (both streams feeding the mux), then the incident
    # shape over the next interval: video flat, audio advancing at its own rate.
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145516}
    # U37: the per-stream watchdog's own reference for the same two pads. Video
    # flat in the airing frontier as well, but for ONE interval only -- well
    # inside ``stall_timeout_s`` -- so this test still proves what it proved
    # before (the line reads interval-relative deltas) without tripping the
    # per-stream judge.
    engine._mux_pad_airing_rt = {"sink_65": 40_000_000_000, "sink_66": 44_000_000_000}
    engine._output_buffers = 100
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()

    clock["t"] = 5.0
    engine._output_buffers = 112
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145750}
    engine._mux_pad_airing_rt = {"sink_65": 40_000_000_000, "sink_66": 44_234_000_000}
    assert engine._check_stall() is True

    err = capsys.readouterr().err
    assert (
        "CTRL output: 112 buffers (+12) since PLAYING [mux-in 5.0s: video=+0 audio=+234]" in err
    ), err

    # ... and the baseline ADVANCES with the print, so the next line reports the
    # NEXT interval rather than re-reporting the same cumulative numbers. (U34:
    # the second interval advances BOTH streams -- a second consecutive interval
    # of video silence is now an outage the per-stream watchdog judges, which is
    # exactly what U34 added; the interval-relative-baseline claim under test
    # does not need video to stay flat twice.)
    clock["t"] = 10.0
    engine._output_buffers = 120
    engine._mux_input_buffers = {"sink_65": 138699, "sink_66": 145984}
    engine._mux_pad_airing_rt = {"sink_65": 40_122_000_000, "sink_66": 44_468_000_000}
    assert engine._check_stall() is True
    assert (
        "CTRL output: 120 buffers (+20) since PLAYING [mux-in 5.0s: video=+122 audio=+234]"
        in capsys.readouterr().err
    )


# --- U34: per-stream stall judgement --------------------------------------------------


def test_per_stream_stall_judges_the_stream_that_stopped_while_the_aggregate_advanced(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """U34: all four 2026-09-25 freezes left the mux SRC counter climbing at the
    audio-only rate while the video branch fed the mux nothing at all, so the
    aggregate check above never judged them and the worker never exited. The
    per-stream check judges the SILENT stream on the same ``stall_timeout_s``
    budget, names it, and exits so the daemon relaunches in seconds.

    U37: "silent" is the stream's AIRING running time at the mux, not its
    arrival count. This test's video pad keeps ARRIVING at its normal rate
    throughout (``_mux_input_buffers`` climbs) while nothing it carries reaches
    air -- the measured U37 shape, which an arrival-count judge cannot see."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._mux_input_pads = {
        "sink_65": _CapsPadStub("sink_65", "video/x-h264, stream-format=byte-stream"),
        "sink_66": _CapsPadStub("sink_66", "audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145516}
    # U37: the judge reads the AIRING running time, and that is exactly the
    # shape the slice measured -- video still ARRIVING at its normal rate
    # (``_mux_input_buffers`` above keeps climbing for both streams) while the
    # mux emits no video at all: ``sink_65``'s airing frontier never moves off
    # its arm-time value for the whole budget.
    engine._mux_pad_airing_rt = {"sink_65": 40_000_000_000, "sink_66": 44_000_000_000}
    engine._output_buffers = 100
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()
    # The live reload-7 shape: the freeze happened after this commit.
    engine._reload_context = (7, "committed")

    # The incident shape for the whole budget: video flat, audio on its own
    # cadence, the aggregate advancing at the audio-only rate throughout.
    for tick in range(1, 10):
        clock["t"] = float(tick)
        engine._output_buffers = 100 + tick * 2
        engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145516 + tick * 47}
        engine._mux_pad_airing_rt = {
            "sink_65": 40_000_000_000,
            "sink_66": 44_000_000_000 + tick * 47_000_000,
        }
        assert engine._check_stall() is True
    assert engine._error is None
    assert engine._loop.quit_calls == 0

    # Past the bound on the SILENT stream -- judged, though the aggregate moved.
    clock["t"] = 10.0
    engine._output_buffers = 122
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145987}
    engine._mux_pad_airing_rt = {"sink_65": 40_000_000_000, "sink_66": 44_470_000_000}
    assert engine._check_stall() is False

    assert engine._error == ("stall", "video stream stalled")
    assert engine._loop.quit_calls == 1
    err = capsys.readouterr().err
    assert (
        "CTRL stall: no video buffers for 10s (last reload id=7 stage=committed) "
        "- quitting for daemon restart" in err
    ), err


def test_per_stream_stall_judges_a_silent_audio_branch_when_the_graph_has_one(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the same class, and the rule for when audio counts:
    ``audio`` is judged iff the persistent output half actually carries an audio
    branch (``audio_selector``), so an audio-less channel is never accused of
    having lost audio."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._mux_input_pads = {
        "sink_65": _CapsPadStub("sink_65", "video/x-h264, stream-format=byte-stream"),
        "sink_66": _CapsPadStub("sink_66", "audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145516}
    # U37: audio still arriving (its counter climbs below), audio never AIRING.
    engine._mux_pad_airing_rt = {"sink_65": 40_000_000_000, "sink_66": 44_000_000_000}
    engine._output_buffers = 100
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()
    # No audio branch in this graph: the (flat) audio pad must NOT be judged.
    assert engine._required_stream_labels() == {"video"}

    engine.audio_selector = object()  # now the graph HAS an audio branch
    assert engine._required_stream_labels() == {"video", "audio"}

    # Audio flat from arm time, video advancing, aggregate advancing.
    clock["t"] = 10.0
    engine._output_buffers = 130
    engine._mux_input_buffers = {"sink_65": 138877, "sink_66": 145516}
    engine._mux_pad_airing_rt = {"sink_65": 40_300_000_000, "sink_66": 44_000_000_000}
    assert engine._check_stall() is False

    assert engine._error == ("stall", "audio stream stalled")
    assert engine._loop.quit_calls == 1
    err = capsys.readouterr().err
    assert (
        "CTRL stall: no audio buffers for 10s (no content reload) - quitting for daemon restart"
        in err
    ), err


def test_per_stream_stall_ignores_caption_pads_and_streams_that_never_produced(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two honest exclusions, both of which would otherwise take a healthy
    channel off air: a caption/subtitle pad is sparse by nature (no cues for
    minutes is normal), and a stream that has produced nothing since the
    reference was armed is the FIRST-OUTPUT budget's question, not this one's."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine.audio_selector = object()  # audio IS a required stream in this graph
    engine._mux_input_pads = {
        "sink_65": _CapsPadStub("sink_65", "video/x-h264, stream-format=byte-stream"),
        "sink_66": _CapsPadStub("sink_66", "audio/mpeg, mpegversion=4"),
        "sink_67": _CapsPadStub("sink_67", "text/x-raw, format=utf8"),
        "sink_68": _CapsPadStub("sink_68", "video/x-h264, stream-format=byte-stream"),
    }
    # sink_68 has never produced a single buffer; sink_67 is the caption pad and
    # is flat throughout; the two REQUIRED streams both keep feeding the mux.
    engine._mux_input_buffers = {"sink_65": 1000, "sink_66": 500, "sink_67": 0, "sink_68": 0}
    # U37: sink_68 has no airing frontier at all (never emitted -- the
    # first-output budget's question) and the caption pad's frontier is frozen
    # at its arm-time value for the whole run. The FRONTIER is the judge's
    # input now, so each exclusion is exercised by the rule that owns it: the
    # caption pad by its label, sink_68 by there being nothing to judge.
    engine._mux_pad_airing_rt = {"sink_65": 1_000_000_000, "sink_66": 500_000_000, "sink_67": 7_000}
    engine._output_buffers = 100
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()

    for tick in range(1, 25):
        clock["t"] = float(tick)
        engine._output_buffers = 100 + tick * 30
        engine._mux_input_buffers = {
            "sink_65": 1000 + tick * 30,
            "sink_66": 500 + tick * 47,
            "sink_67": 0,
            "sink_68": 0,
        }
        engine._mux_pad_airing_rt = {
            "sink_65": 1_000_000_000 + tick * 30_000_000,
            "sink_66": 500_000_000 + tick * 47_000_000,
            "sink_67": 7_000,
        }
        assert engine._check_stall() is True, f"tick {tick}"
    assert engine._error is None
    assert engine._loop.quit_calls == 0


def test_per_stream_stall_judges_a_stream_that_keeps_advancing_but_runs_far_behind(
    engine_module, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """U37's second disjunct, and the shape the slice actually measured live.

    Both streams keep advancing, so the flat rule above never has anything to
    judge -- but video's airing running time sits 9.4 s behind audio's and stays
    there for the whole budget, which is what the mux looks like while it emits
    audio alone: a stream the mux has already passed cannot air in step, however
    steadily it keeps arriving and advancing. 9.4 s is the lag the collapsed
    captures measured in the mux's own recorded output (9.408-9.429 s of video
    running time behind audio); the bound is ``_STREAM_LAG_BOUND_S`` = 2.0 s,
    chosen because 39
    healthy recordings off the real LPM media never showed a worst lag above
    0.726678 s -- the media's own A/V end offset at the boundary, not a defect --
    while the three collapses sat at 9.408-9.429 s.

    The flat rule keeps the attribution when both could fire: see
    ``test_per_stream_stall_judges_the_stream_that_stopped_while_the_aggregate_advanced``,
    whose frozen video is ALSO 4 s behind its audio and is still named by the
    flat wording. This test proves the lag wording is what fires when nothing
    has stopped."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine.audio_selector = object()  # audio IS a required stream in this graph
    engine._mux_input_pads = {
        "sink_65": _CapsPadStub("sink_65", "video/x-h264, stream-format=byte-stream"),
        "sink_66": _CapsPadStub("sink_66", "audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 138577, "sink_66": 145516}
    # Both frontiers advance on every tick, at the SAME rate: the 9.4 s gap is
    # constant, so nothing here is a stream that stopped.
    engine._mux_pad_airing_rt = {"sink_65": 40_000_000_000, "sink_66": 49_400_000_000}
    engine._output_buffers = 100
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()
    engine._reload_context = (7, "committed")

    for tick in range(1, 11):
        clock["t"] = float(tick)
        engine._output_buffers = 100 + tick * 2
        engine._mux_input_buffers = {"sink_65": 138577 + tick * 47, "sink_66": 145516 + tick * 47}
        engine._mux_pad_airing_rt = {
            "sink_65": 40_000_000_000 + tick * 30_000_000,
            "sink_66": 49_400_000_000 + tick * 30_000_000,
        }
        assert engine._check_stall() is True, f"tick {tick}"
    assert engine._error is None
    assert engine._loop.quit_calls == 0

    clock["t"] = 11.0
    engine._output_buffers = 122
    engine._mux_input_buffers = {"sink_65": 139094, "sink_66": 146033}
    engine._mux_pad_airing_rt = {
        "sink_65": 40_330_000_000,
        "sink_66": 49_730_000_000,
    }
    assert engine._check_stall() is False

    # Hostility check: the video pad advanced on THIS very tick, so the flat rule
    # had nothing to judge and cannot be the rule that fired.
    assert engine._stream_last_advance_t["sink_65"] == 11.0
    assert engine._error == ("stall", "video stream lagging audio")
    assert engine._loop.quit_calls == 1
    err = capsys.readouterr().err
    assert (
        "CTRL stall: video running time 9.4s behind audio for 10s "
        "(last reload id=7 stage=committed) - quitting for daemon restart" in err
    ), err


def test_per_stream_lag_must_be_continuous_to_be_judged(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U37: a lag that comes back within the bound starts over.

    This is the property that makes the bound safe at the switch boundary itself
    -- an A/V end offset lags the ending stream for about the length of the
    offset, so a lag clock that kept running across a recovery would judge the
    next boundary on the previous boundary's seconds. Video here runs 9.4 s
    behind for nine ticks (one short of the budget), catches up across two ticks
    while audio keeps airing, and only then falls 9.4 s behind again -- and the
    judgement lands ten seconds after the SECOND lag began, not eleven after the
    first. A judge whose clock never cleared would fire at tick 11, where the
    lag is already back within the bound.

    Every frontier here moves forward, as a real one does."""
    clock = {"t": 0.0}
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(engine_module.GLib, "timeout_add_seconds", lambda *a, **k: None)
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine.audio_selector = object()
    engine._mux_input_pads = {
        "sink_65": _CapsPadStub("sink_65", "video/x-h264, stream-format=byte-stream"),
        "sink_66": _CapsPadStub("sink_66", "audio/mpeg, mpegversion=4"),
    }
    engine._mux_input_buffers = {"sink_65": 0, "sink_66": 0}
    engine._mux_pad_airing_rt = {"sink_65": 40_000_000_000, "sink_66": 49_400_000_000}
    engine._output_buffers = 100
    engine._arm_stall_watchdog()
    engine._loop = _FakeLoop()

    def tick(t: int, video: int, audio: int, expect: bool) -> None:
        clock["t"] = float(t)
        engine._output_buffers = 100 + t * 2
        engine._mux_pad_airing_rt = {"sink_65": video, "sink_66": audio}
        assert engine._check_stall() is expect, f"tick {t}"

    # 9.4 s behind, one tick short of the budget.
    for t in range(1, 10):
        tick(t, 40_000_000_000 + t * 30_000, 49_400_000_000 + t * 30_000, expect=True)
    # Catching up: the gap closes from 9.4 s to 4.43 s to 0.04 s -- under the
    # bound only on the third tick, so the clock runs through the first two.
    tick(10, 45_270_000_000, 49_700_000_000, expect=True)
    tick(11, 49_770_000_000, 49_730_000_000, expect=True)
    assert "sink_65" not in engine._stream_lag_since, "the clock did not restart"

    # Audio runs away again (the live shape: audio airs alone), video follows.
    tick(12, 49_800_000_000, 59_160_000_000, expect=True)
    for t in range(13, 22):
        tick(t, 49_800_000_000 + (t - 12) * 30_000, 59_160_000_000 + (t - 12) * 30_000, expect=True)
    assert engine._error is None, "fired on the FIRST lag's clock, not the second's"
    assert engine._loop.quit_calls == 0

    tick(22, 50_100_000_000, 59_460_000_000, expect=False)
    assert engine._error == ("stall", "video stream lagging audio")
    assert engine._loop.quit_calls == 1


# --- round-2 finding 2: the stall bound yields to the commit watchdog ---------------


def _stall_engine_at_the_bound(engine_module, monkeypatch: pytest.MonkeyPatch, clock: dict) -> Any:
    """An engine that has seen real output and has then been stalled for exactly
    ``stall_timeout_s``, i.e. sitting on the trigger."""
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock["t"])
    engine = _bare_engine(engine_module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._loop = _FakeLoop()
    engine._first_output_seen = True
    engine._output_buffers = 100
    engine._stall_last_count = 100
    engine._stall_last_advance_t = clock["t"]
    clock["t"] += 10.0
    return engine


def test_stall_bound_does_not_kill_the_worker_while_a_commit_is_in_progress(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-2 finding 2: the two watchdogs were racing and the wrong one won.

    A commit holds the replacement leg (producing nothing) while the old leg is
    retired, and the isolation queues drain in well under a second, so output
    legitimately stops advancing for the length of the retirement. With
    ``stall_timeout_s`` at 10s and ``commit_timeout_s`` at 15s the stall watchdog
    therefore always fired FIRST -- five seconds before the commit watchdog could
    dump the faulthandler stacks that exist precisely to localise a wedged
    retirement, which is why that dump was never seen in production."""
    clock = {"t": 0.0}
    engine = _stall_engine_at_the_bound(engine_module, monkeypatch, clock)
    engine._pending_reload = {"commit_in_progress": True}

    assert engine._check_stall() is True  # suspended, not fatal
    assert engine._loop.quit_calls == 0
    assert engine._error is None


def test_stall_bound_still_kills_the_worker_outside_a_commit(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suspension is scoped to commits; the ordinary bound is unchanged."""
    clock = {"t": 0.0}
    engine = _stall_engine_at_the_bound(engine_module, monkeypatch, clock)

    assert engine._check_stall() is False
    assert engine._loop.quit_calls == 1
    assert engine._error == ("stall", "output stalled")


def test_a_reload_armed_but_not_committing_does_not_suspend_the_stall_bound(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an IN-FLIGHT commit owns the window. A reload that is merely armed --
    prepared, held, waiting for its boundary -- leaves the current program on air
    and must not buy it an exemption from the stall bound."""
    clock = {"t": 0.0}
    engine = _stall_engine_at_the_bound(engine_module, monkeypatch, clock)
    engine._pending_reload = {"commit_in_progress": False}

    assert engine._check_stall() is False
    assert engine._loop.quit_calls == 1


def test_a_commit_that_finishes_gets_a_full_fresh_stall_budget(
    engine_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suspension pushes the stall reference forward rather than skipping one
    tick, so a commit that completes cleanly is measured against a FULL budget
    instead of the backlog it accumulated while suspended -- otherwise the first
    post-commit tick would fire immediately on a healthy channel."""
    clock = {"t": 0.0}
    engine = _stall_engine_at_the_bound(engine_module, monkeypatch, clock)
    engine._pending_reload = {"commit_in_progress": True}

    for _ in range(5):  # five seconds of retirement
        assert engine._check_stall() is True
        clock["t"] += 1.0

    # Commit finished. ``_finish_reload_commit`` calls ``_reset_stall_reference``
    # on both its exits; do the same here, since this test drives ``_check_stall``
    # directly rather than through a real commit.
    engine._pending_reload = None
    engine._reset_stall_reference()
    for _ in range(10):  # a full fresh 10s budget, none of it already spent
        assert engine._check_stall() is True, "post-commit budget was not reset"
        clock["t"] += 1.0

    assert engine._check_stall() is False  # and the bound still bites at 10s
    assert engine._loop.quit_calls == 1


def test_output_health_reports_measured_rates_and_zero_on_stall(engine_module, capsys):
    from civiccast.egress.health import parse_ffmpeg_encoder_metrics_line

    engine = object.__new__(engine_module.GstPlayoutEngine)
    engine._last_output_progress_print_t = 10.0
    engine._mux_input_snapshot_t = 10.0
    engine._output_buffers = 100
    engine._output_buffers_at_arm = 0
    engine._output_bytes = 3_750_000
    engine._output_bytes_snapshot = 0
    engine._mux_input_delta_suffix = lambda elapsed: "[mux-in 5.0s: video=+150 audio=+200]"
    for name in (
        "_u59_judge_leg_rate",
        "_u59_judge_leg_lag",
        "_snapshot_mux_input",
        "_snapshot_chain_input",
    ):
        setattr(engine, name, lambda *args: None)
    engine._maybe_print_output_progress(15.0)
    measured = parse_ffmpeg_encoder_metrics_line(capsys.readouterr().err)
    assert measured.encoder_fps == 30
    assert measured.encoder_bitrate_kbps == 6000
    engine._mux_input_delta_suffix = lambda elapsed: "[mux-in 5.0s: video=+0 audio=+0]"
    engine._maybe_print_output_progress(20.0)
    stalled = parse_ffmpeg_encoder_metrics_line(capsys.readouterr().err)
    assert stalled.encoder_fps == 0
    assert stalled.encoder_bitrate_kbps == 0


# --- U37 lag half vs the graph's own declared audio latency ---------------------


class _FakeCaps:
    def __init__(self, text: str) -> None:
        self._text = text

    def to_string(self) -> str:
        return self._text


class _FakeMuxPad:
    def __init__(self, caps: str) -> None:
        self._caps = _FakeCaps(caps)

    def get_current_caps(self) -> _FakeCaps:
        return self._caps


def _stream_judge_engine(module: types.ModuleType, *, audio_latency_ns: int) -> Any:
    """A bare engine whose only populated judge inputs are the two mux streams."""
    engine = _bare_engine(module, first_output_timeout_s=45.0, stall_timeout_s=10.0)
    engine._mux_input_pads = {
        "sink_65": _FakeMuxPad("video/x-h264"),
        "sink_66": _FakeMuxPad("audio/x-ac3"),
    }
    engine.audio_selector = object()
    engine.output_audio_latency_ns = audio_latency_ns
    engine.fired: list[str] = []
    engine._act_on_stream_stall = lambda **kwargs: engine.fired.append(kwargs["headline"]) or False
    return engine


def _drive_audio_lag(engine: Any, *, behind_s: float, ticks: int = 14) -> None:
    """Both streams advance at 1s/s; audio airs ``behind_s`` behind video."""
    for tick in range(ticks):
        now = float(tick)
        video_rt = int(now * 1_000_000_000)
        engine._mux_pad_airing_rt["sink_65"] = video_rt
        engine._mux_pad_airing_rt["sink_66"] = video_rt - int(behind_s * 1_000_000_000)
        engine._check_stream_stalls(now)


def test_stream_lag_judge_does_not_fire_at_the_declared_audio_latency(
    engine_module,
) -> None:
    """The loudnorm branch declares 3.0s of audio latency; a 2.9s lag is by design.

    Measured on the 5060 Ti station (2026-11-19): every ``audio running time
    ... behind video`` exit named 2.1-3.0s -- the declared latency -- and they
    stopped when the cable sink whose atsc-a85 target builds that branch was
    removed from the channel.
    """
    engine = _stream_judge_engine(engine_module, audio_latency_ns=3_000_000_000)
    _drive_audio_lag(engine, behind_s=2.9)
    assert engine.fired == []


def test_stream_lag_judge_still_fires_past_declared_latency_plus_bound(
    engine_module,
) -> None:
    engine = _stream_judge_engine(engine_module, audio_latency_ns=3_000_000_000)
    _drive_audio_lag(engine, behind_s=6.0)
    assert engine.fired, "a 6.0s lag is past 2.0s + the 3.0s the graph declares"
    assert "audio running time" in engine.fired[0]


def test_stream_lag_judge_keeps_the_fixed_bound_without_declared_latency(
    engine_module,
) -> None:
    engine = _stream_judge_engine(engine_module, audio_latency_ns=0)
    _drive_audio_lag(engine, behind_s=2.9)
    assert engine.fired, "no declared latency: 2.9s is still past the 2.0s bound"
