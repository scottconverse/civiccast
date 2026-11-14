# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""BETA.10 U16 item B: the daemon's output A/V sync guard.

The U16 finding (2026-09-24) is a one-sided rebase step: the GStreamer engine's
content reload moved ONE leg's timestamps and left the other alone, so the
program leaving the encoder had its audio and its video offset by ~109s. Nothing
in the egress path looked at the OUTPUT program's own A/V relationship, so the
only place that step was ever visible was a human watching the channel.

This guard is the cheap output-side self-check: for a channel that is genuinely
ON AIR, every ~30s, measure the first packet PTS of video and of audio in the
newest COMPLETE segment of the channel's HLS window. A one-off difference is
normal (segment cuts land on video keyframes, and the audio in a cut segment can
legitimately lead or lag by a fraction of a frame). A difference that persists
across three consecutive probes is a fault the operator cannot fix and the
channel cannot air through, so the guard restarts that channel's WORKER -- the
same restart a dead encoder gets -- under a hard budget. Once the rolling-hour
budget is spent, the channel uses its fallback slate through the cooldown and
gets one program retry when the most recent restart ages out.

Three disciplines this file pins, all inherited from U12's relay probe:

* Tri-state by presence. ``None`` from the probe means "no measurement" (no
  complete segment yet, a segment missing one of the kinds, ffprobe absent,
  failed, or timed out). It NEVER advances the streak, never resets it, and can
  never by itself restart anything -- a broken probe tool must not become a
  restart source.
* Never judge a channel that is not ON_AIR. A STARTING or FALLBACK_SLATE
  channel has no settled output to judge, and a channel mid-reload reads
  TRANSITIONING because the rebase step legitimately moves A/V against each
  other for a moment.
* Reset the streak when the worker pid changes. Measurements describe the
  worker that produced them; a replacement worker's first segment says nothing
  about its predecessor's last one.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest

from civiccast.egress.daemon import (
    _OUTPUT_AV_GUARD_BUDGET_WINDOW_S,
    _OUTPUT_AV_GUARD_CONSECUTIVE_PROBES,
    _OUTPUT_AV_GUARD_EXHAUSTED_LOG_INTERVAL_S,
    _OUTPUT_AV_GUARD_MAX_OFFSET_S,
    _OUTPUT_AV_GUARD_PROBE_INTERVAL_S,
    _OUTPUT_AV_GUARD_RESTART_BUDGET,
    EgressDaemon,
)
from civiccast.egress.hls_relay import (
    HlsRelaySupervisor,
    _last_complete_segment,
    _segment_first_packet_pts,
)
from civiccast.egress.models import EgressCommand, EgressConfig, EgressSinkSpec
from civiccast.egress.source_plan import EgressSourcePlan, EgressSourceSegment
from civiccast.egress.store import InMemoryEgressStore
from civiccast.stream._ffmpeg import run_ffmpeg

#: The daemon module's own logger -- the guard's ERROR line must reach an
#: operator through it, not through a print or a proof event alone.
_DAEMON_LOGGER = "civiccast.egress.daemon"


# --- fixtures and doubles --------------------------------------------------------


class _FakeClock:
    def __init__(self, value: float = 50_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _FakeWorker:
    """A worker double whose exit is faithful to a REAL deliberate kill.

    Windows' ``Popen.terminate`` is ``TerminateProcess(handle, 1)`` and POSIX
    ``SIGTERM``/``SIGKILL`` both report a negative code, so a terminated worker
    exits NON-ZERO in production. That matters here: a non-zero exit with no
    pending reload is exactly what routes the daemon into
    ``_relaunch_after_crash`` -- the dead-encoder path this guard reuses. (The
    ``_FakeProcess`` in ``test_hls_relay_progress.py`` sets ``returncode = 0``,
    which would instead take the clean-exit branch.)
    """

    def __init__(self, *, pid: int, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode: int | None = returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    def kill(self) -> None:
        self.killed = True
        self.returncode = 1


class _RecordingProbe:
    """A stand-in for the segment A/V probe, consuming a scripted value list.

    Values are ``(video_pts, audio_pts)`` or ``None`` ("no measurement").
    Once the list is exhausted every further call answers ``None`` -- so a test
    that scripts too few values fails loudly on its assertions, never by
    silently restarting something.
    """

    def __init__(self, values: Sequence[tuple[float, float] | None]) -> None:
        self.values = list(values)
        self.paths: list[Path] = []

    @property
    def calls(self) -> int:
        return len(self.paths)

    def __call__(self, path: Path) -> tuple[float, float] | None:
        self.paths.append(path)
        if not self.values:
            return None
        return self.values.pop(0)


class _GuardFixture(NamedTuple):
    daemon: EgressDaemon
    store: InMemoryEgressStore
    workers: list[_FakeWorker]
    clock: _FakeClock
    hls_dir: Path


def _write_playlist(directory: Path, *, segments: tuple[str, ...], closed: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    playlist = directory / "playlist.m3u8"
    body = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
    body += "".join(f"#EXTINF:2.000,\n{name}\n" for name in segments)
    if closed:
        body += "#EXT-X-ENDLIST\n"
    playlist.write_text(body, encoding="utf-8")
    return playlist


def _source_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "source-a.ts"
    source.write_text("fake", encoding="utf-8")
    return EgressSourcePlan(
        channel_id="gov",
        segments=[
            EgressSourceSegment(
                label="Council meeting",
                path=str(source),
                duration_seconds=1,
                source_ref="asset-council",
            )
        ],
    )


def _start_command() -> EgressCommand:
    return EgressCommand(
        channel_id="gov",
        action="start",
        issued_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        issued_by="operator",
        command_id="cmd-start",
    )


def _guard_fixture(
    tmp_path: Path,
    *,
    hls_dir: Path | None = None,
    segments: tuple[str, ...] = ("seg000000001.ts", "seg000000002.ts"),
    hls_relay_supervisor: HlsRelaySupervisor | None = None,
) -> _GuardFixture:
    """A daemon whose one ON_AIR channel has an HLS window on disk.

    ``segments`` names the playlist's entries in order; the LAST one is the
    entry the muxer may still be writing, so the guard must measure the one
    before it (see ``_last_complete_segment``).
    """
    live_dir = hls_dir if hls_dir is not None else tmp_path / "gov-live"
    _write_playlist(live_dir, segments=segments)

    workers: list[_FakeWorker] = []

    def starter(_args: list[str]) -> _FakeWorker:
        worker = _FakeWorker(pid=4242 + len(workers))
        workers.append(worker)
        return worker

    clock = _FakeClock()
    store = InMemoryEgressStore()
    store.upsert_config(
        EgressConfig(
            channel_id="gov",
            enabled=True,
            slate_message="slate",
            sinks=[EgressSinkSpec(kind="hls", label="Web", uri=str(live_dir))],
        )
    )
    store.enqueue_command(_start_command())
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=starter,
        hls_relay_supervisor=hls_relay_supervisor,
    )
    daemon._monotonic = clock  # type: ignore[method-assign]
    return _GuardFixture(daemon=daemon, store=store, workers=workers, clock=clock, hls_dir=live_dir)


def _probe_ticks(fixture: _GuardFixture, count: int, *, step: float = 30.0) -> None:
    """Advance the clock and run one guard pass per step."""
    for _ in range(count):
        fixture.clock.value += step
        fixture.daemon._poll_output_av_guard("gov")


def _swap_worker(fixture: _GuardFixture, *, pid: int) -> _FakeWorker:
    """Replace the tracked worker exactly the way a relaunch does.

    ``_start`` writes the new process into ``_processes`` and restamps
    ``_started_at``; a test that needs N guard episodes without driving the real
    relaunch path does the same two writes.
    """
    replacement = _FakeWorker(pid=pid)
    fixture.workers.append(replacement)
    fixture.daemon._processes["gov"] = replacement
    fixture.daemon._started_at["gov"] = fixture.clock.value
    return replacement


def _error_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


# --- the shipped bounds ----------------------------------------------------------


def test_u16_guard_bounds_match_the_authorized_values() -> None:
    """The numbers are the coordinator's spec, not taste: ~30s probes, a 1.0s
    limit, 3 consecutive probes, 3 restarts per rolling hour, then a 10-minute
    ERROR cadence."""
    assert _OUTPUT_AV_GUARD_PROBE_INTERVAL_S == 30.0
    assert _OUTPUT_AV_GUARD_MAX_OFFSET_S == 1.0
    assert _OUTPUT_AV_GUARD_CONSECUTIVE_PROBES == 3
    assert _OUTPUT_AV_GUARD_RESTART_BUDGET == 3
    assert _OUTPUT_AV_GUARD_BUDGET_WINDOW_S == 3600.0
    assert _OUTPUT_AV_GUARD_EXHAUSTED_LOG_INTERVAL_S == 600.0


# --- the complete-segment rule ---------------------------------------------------


def test_u16_last_complete_segment_skips_the_entry_still_being_written(
    tmp_path: Path,
) -> None:
    """A live playlist's final entry is the segment the muxer is writing, so the
    newest COMPLETE one is the entry before it."""
    playlist = _write_playlist(
        tmp_path / "live",
        segments=("seg000000001.ts", "seg000000002.ts", "seg000000003.ts"),
    )

    assert _last_complete_segment(playlist) == "seg000000002.ts"


def test_u16_last_complete_segment_uses_the_final_entry_when_the_window_closed(
    tmp_path: Path,
) -> None:
    """A closed window (``#EXT-X-ENDLIST``) has no in-flight segment, so its
    final entry IS complete and must be the newest one."""
    playlist = _write_playlist(
        tmp_path / "live",
        segments=("seg000000001.ts", "seg000000002.ts", "seg000000003.ts"),
        closed=True,
    )

    assert _last_complete_segment(playlist) == "seg000000003.ts"


def test_u16_last_complete_segment_is_none_without_a_complete_segment(
    tmp_path: Path,
) -> None:
    """Tri-state by presence: one entry is the one being written, and a missing
    or unparsable playlist has nothing to measure. ``None``, never a guess."""
    single = _write_playlist(tmp_path / "young", segments=("seg000000001.ts",))
    empty = _write_playlist(tmp_path / "empty", segments=())

    assert _last_complete_segment(single) is None
    assert _last_complete_segment(empty) is None
    assert _last_complete_segment(tmp_path / "absent" / "playlist.m3u8") is None


# --- streak logic ----------------------------------------------------------------


def test_u16_guard_restarts_only_after_three_consecutive_over_limit_probes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two over-limit probes are not yet a fault; the third restarts the worker.

    The restart is a deliberate kill of the LIVE worker (`terminate` on the
    process the daemon tracks) -- nothing is restarted by the guard itself; the
    daemon's own exit handling owns the replacement.
    """
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe([(0.0, 2.5)] * 3)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1
    assert fixture.store.read_state("gov") is not None
    assert fixture.store.read_state("gov").state == "ON_AIR"  # type: ignore[union-attr]

    with caplog.at_level(logging.ERROR, logger=_DAEMON_LOGGER):
        _probe_ticks(fixture, 2)
        assert probe.calls == 2
        assert probe.paths[0] == fixture.hls_dir / "seg000000001.ts"
        assert not fixture.workers[0].terminated
        assert not _error_lines(caplog)

        _probe_ticks(fixture, 1)

    assert probe.calls == 3
    assert fixture.workers[0].terminated
    assert fixture.workers[0].poll() == 1
    # ONE ERROR line, naming the channel, all three measurements and the
    # segment each came from.
    lines = _error_lines(caplog)
    assert len(lines) == 1
    message = lines[0]
    assert "gov" in message
    assert "seg000000001.ts" in message
    assert "2.500" in message
    assert "restart" in message.lower()


def test_u16_guard_between_probes_is_throttled_to_the_interval(tmp_path: Path) -> None:
    """The probe cadence is the interval, not the tick rate: a daemon ticked
    every 2s must not probe (and so must not accumulate a streak) 15x faster."""
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe([(0.0, 2.5)] * 10)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1
    # The first tick probes (nothing has been measured yet); the next 9 carry
    # the clock only 18s further, inside the 30s cadence.
    for _ in range(10):
        fixture.clock.value += 2.0
        fixture.daemon._poll_output_av_guard("gov")
    assert probe.calls == 1

    # One full interval later the next probe is due.
    fixture.clock.value += _OUTPUT_AV_GUARD_PROBE_INTERVAL_S
    fixture.daemon._poll_output_av_guard("gov")
    assert probe.calls == 2

    assert not fixture.workers[0].terminated


def test_u16_guard_no_measurement_neither_advances_nor_resets_the_streak(
    tmp_path: Path,
) -> None:
    """A probe that cannot measure is not evidence in either direction.

    The scripted sequence is bad / None / bad / None / bad: the two ``None``
    probes neither count toward the streak nor clear it, so the third BAD
    measurement still restarts -- and no ``None`` ever restarts anything.
    """
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe([(0.0, 2.5), None, (0.0, 2.5), None, (0.0, 2.5)])
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1

    for index in range(4):
        _probe_ticks(fixture, 1)
        assert probe.calls == index + 1
        # Probe 4 is the third over-limit one, but the None before it broke the
        # run, so the streak is only 2 here and nothing may restart yet.
        assert not fixture.workers[0].terminated, f"probe #{index + 1} must not restart"

    _probe_ticks(fixture, 1)

    assert probe.calls == 5
    assert fixture.workers[0].terminated


def test_u16_guard_an_in_sync_probe_resets_the_streak(tmp_path: Path) -> None:
    """Consecutive means consecutive: a measurement inside the limit clears the
    streak, so the fault must build from scratch again."""
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe(
        [(0.0, 2.5), (0.0, 2.5), (0.0, 0.1), (0.0, 2.5), (0.0, 2.5), (0.0, 2.5)]
    )
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1

    _probe_ticks(fixture, 5)  # bad, bad, IN SYNC (reset), bad, bad
    assert probe.calls == 5
    assert not fixture.workers[0].terminated

    _probe_ticks(fixture, 1)
    assert fixture.workers[0].terminated


def test_u16_guard_a_new_worker_pid_resets_the_streak(tmp_path: Path) -> None:
    """Measurements describe the worker that produced them: two bad probes from
    worker A followed by two from worker B is not a three-probe streak."""
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe([(0.0, 2.5)] * 5)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1

    _probe_ticks(fixture, 2)
    assert not fixture.workers[0].terminated

    # A relaunch replaced the worker (the daemon tracks the new process and
    # resets its own per-worker bookkeeping the same way).
    replacement = _swap_worker(fixture, pid=9001)

    _probe_ticks(fixture, 2)
    assert probe.calls == 4
    assert not replacement.terminated

    _probe_ticks(fixture, 1)
    assert replacement.terminated


# --- the judged-state gate -------------------------------------------------------
#
# U21 item B2 widened this gate from ON_AIR to ON_AIR + FALLBACK_SLATE. A slate
# leg is written by the same mux, the same udpsink and the same long-lived relay
# child as a program leg, so it can carry -- and strand -- the same output A/V
# offset; the live slate-entry incident was measured on a slate leg, and B1's
# relay rebind cannot cure an offset born on a slate the worker never restarts
# out of. Everything else is gate-kept exactly as before.


def test_u16_guard_judges_a_fallback_slate_output(tmp_path: Path) -> None:
    """A settled slate is judged like any other settled output: three consecutive
    over-limit probes restart the worker through the ordinary crash-relaunch path."""
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe([(0.0, 2.5)] * 3)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1
    fixture.daemon._write_state("gov", "FALLBACK_SLATE")

    _probe_ticks(fixture, 2)
    assert probe.calls == 2, "a slate output was not measured at all"
    assert not fixture.workers[0].terminated, "two slate probes are not yet a fault"

    _probe_ticks(fixture, 1)

    assert probe.calls == 3
    assert fixture.workers[0].terminated
    assert fixture.workers[0].poll() == 1


def test_u16_guard_does_not_judge_a_channel_with_no_settled_output(tmp_path: Path) -> None:
    """The slate half of the gate widened; the rest of it did not.

    STARTING has no output yet; TRANSITIONING is what ``_poll_process`` publishes
    for a channel with a content reload in flight -- exactly the moment the U16
    rebase step happens and A/V legitimately moves against itself; and a draining
    channel is deliberately leaving air. None of the three is a settled output,
    so none may be probed or restarted. Proving all three here keeps the widened
    gate from being satisfied by an unrelated early return.

    The state row is written per tick and the guard is polled directly: the
    daemon's own writer preserves FALLBACK_SLATE and DRAINING but re-publishes
    ON_AIR for a live worker, which is not a state this test is about.
    """
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe([(0.0, 2.5)] * 12)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1

    for state in ("STARTING", "TRANSITIONING"):
        for _ in range(3):
            fixture.daemon._write_state("gov", state)
            fixture.clock.value += 30.0
            fixture.daemon._poll_output_av_guard("gov")
        assert probe.calls == 0, f"{state} was judged"
        assert not fixture.workers[0].terminated, f"{state} restarted the worker"

    # Draining is a channel on its way off air, whatever state row it still holds.
    fixture.daemon._draining_channels.add("gov")
    for _ in range(3):
        fixture.daemon._write_state("gov", "ON_AIR")
        fixture.clock.value += 30.0
        fixture.daemon._poll_output_av_guard("gov")
    assert probe.calls == 0, "a draining channel was judged"
    assert not fixture.workers[0].terminated

    # And the same channel, settled on air, is judged.
    fixture.daemon._draining_channels.discard("gov")
    fixture.daemon._write_state("gov", "ON_AIR")
    _probe_ticks(fixture, 1)
    assert probe.calls == 1
    assert not fixture.workers[0].terminated


def test_u16_guard_slate_restart_rebinds_the_hls_relay_to_the_new_worker_session(
    tmp_path: Path,
) -> None:
    """U21 B2: the slate restart must take B1's path -- worker AND relay.

    A slate desync is by construction the case B1 cannot pre-empt: the worker is
    healthy, it is the relay child's own corrected timeline that is wrong, so the
    guard's kill is the only thing that ever replaces either. The replacement
    worker must therefore get a relay bound to ITS session, not the desynced one
    still writing the channel's playlist."""
    relay_calls: list[list[str]] = []
    relay_procs: list[_FakeWorker] = []

    def relay_starter(args: list[str], *, stderr_path: Path | None = None) -> _FakeWorker:
        relay_calls.append(args)
        proc = _FakeWorker(pid=9000 + len(relay_calls))
        relay_procs.append(proc)
        return proc

    supervisor = HlsRelaySupervisor(starter=relay_starter)
    fixture = _guard_fixture(tmp_path, hls_relay_supervisor=supervisor)
    # One clock for daemon AND relay supervisor. Left on the real monotonic
    # clock, the relay's startup grace was judged as ``fake now - real start``,
    # which passed or not depending on the machine's uptime (CI Linux: passed,
    # so the slate stall heal fired; a long-up Windows box: never).
    supervisor._clock = fixture.clock
    probe = _RecordingProbe([(0.0, 2.5)] * 3)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1
    assert len(relay_calls) == 1, "the first worker session did not start a relay"
    fixture.daemon._write_state("gov", "FALLBACK_SLATE")

    # A slate desync is a LIVE relay window with a wrong A/V offset: the window
    # keeps advancing. A frozen window is the stall-heal case instead (see
    # tests/egress/test_slate_hls_health.py), and that heal clears the window
    # the guard measures, so the two must not be mixed in this test.
    for tick in range(_OUTPUT_AV_GUARD_CONSECUTIVE_PROBES):
        _write_playlist(
            fixture.hls_dir,
            segments=(f"seg{tick + 1:09d}.ts", f"seg{tick + 2:09d}.ts"),
        )
        fixture.clock.value += _OUTPUT_AV_GUARD_PROBE_INTERVAL_S
        fixture.daemon.process_once("gov")

    assert fixture.workers[0].terminated, "the slate desync did not restart the worker"

    # The next ordinary tick replaces the killed worker; the replacement session
    # must rebind the channel's relay.
    fixture.clock.value += 2.0
    fixture.daemon.process_once("gov")

    assert len(fixture.workers) == 2, "the worker was not relaunched"
    assert len(relay_calls) == 2, "the relaunched worker inherited the desynced relay"
    assert relay_calls[0] == relay_calls[1], "the rebound relay must keep the same udp port"
    assert relay_procs[0].terminated
    assert not relay_procs[1].terminated


# --- budget ----------------------------------------------------------------------


def test_u16_guard_restart_budget_is_three_per_rolling_hour(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Three restarts inside the hour, then one slate cooldown and program retry."""
    fixture = _guard_fixture(tmp_path)
    forced_slate: dict[str, str] = {}
    fallback_requests: list[str] = []
    program_retries: list[str] = []

    def request_fallback_slate(*, channel_id: str, reason: str) -> None:
        fallback_requests.append(channel_id)
        forced_slate[channel_id] = reason

    def has_fallback_slate_reason(*, channel_id: str, reason: str) -> bool:
        return forced_slate.get(channel_id) == reason

    def request_slate_exit(*, channel_id: str) -> None:
        program_retries.append(channel_id)
        forced_slate.pop(channel_id, None)

    fixture.daemon.request_fallback_slate = request_fallback_slate  # type: ignore[attr-defined]
    fixture.daemon.has_fallback_slate_reason = has_fallback_slate_reason  # type: ignore[attr-defined]
    fixture.daemon.request_slate_exit = request_slate_exit  # type: ignore[attr-defined]
    # Six episodes' worth of over-limit measurements (plus slack): episodes 1-3
    # restart, 4-6 are refused and only logged.
    probe = _RecordingProbe([(0.0, 2.5)] * 30)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1

    with caplog.at_level(logging.ERROR, logger=_DAEMON_LOGGER):
        # Three episodes, each ending in a restart. A relaunch replaces the
        # worker, so the streak starts over for each -- the same pid-change
        # reset the daemon gets from a real relaunch.
        for episode in range(_OUTPUT_AV_GUARD_RESTART_BUDGET):
            _probe_ticks(fixture, _OUTPUT_AV_GUARD_CONSECUTIVE_PROBES)
            terminated = [worker for worker in fixture.workers if worker.terminated]
            assert len(terminated) == episode + 1
            _swap_worker(fixture, pid=8000 + episode)

        assert len(_error_lines(caplog)) == _OUTPUT_AV_GUARD_RESTART_BUDGET

        # Fourth episode: the streak keeps growing, the verdict is refused.
        _probe_ticks(fixture, _OUTPUT_AV_GUARD_CONSECUTIVE_PROBES)
        assert len([worker for worker in fixture.workers if worker.terminated]) == 3
        exhausted = _error_lines(caplog)
        assert len(exhausted) == _OUTPUT_AV_GUARD_RESTART_BUDGET + 1
        assert "budget" in exhausted[-1].lower()
        assert "gov" in exhausted[-1]
        assert "2.500" in exhausted[-1]

        assert fallback_requests == ["gov"]
        recovery = fixture.daemon._quality_recovery["gov"]

        # The detector stays suppressed for the whole cooldown; it does not
        # repeat the slate request or spend more restarts on each poll.
        _probe_ticks(fixture, _OUTPUT_AV_GUARD_CONSECUTIVE_PROBES)
        assert len(_error_lines(caplog)) == _OUTPUT_AV_GUARD_RESTART_BUDGET + 1
        assert fallback_requests == ["gov"]

        fixture.clock.value = recovery.retry_at - 1.0
        fixture.daemon._poll_output_av_guard("gov")
        assert len([worker for worker in fixture.workers if worker.terminated]) == 3
        assert program_retries == []

        fixture.clock.value = recovery.retry_at
        fixture.daemon._poll_quality_recovery("gov")
        assert program_retries == ["gov"]
        assert "gov" not in fixture.daemon._quality_recovery
        assert len([worker for worker in fixture.workers if worker.terminated]) == 3

        # The program retry happens only after the latest restart ages out.
        _probe_ticks(fixture, _OUTPUT_AV_GUARD_CONSECUTIVE_PROBES)
        assert len([worker for worker in fixture.workers if worker.terminated]) == 4


# --- the restart is the dead-encoder path ----------------------------------------


def test_u16_guard_restart_rides_the_dead_encoder_relaunch_path(tmp_path: Path) -> None:
    """The guard's kill must reach ``_relaunch_after_crash`` -- the same path a
    crashed encoder takes -- rather than a parallel restart implementation.

    Driven through real ``process_once`` ticks: the kill lands on the tick the
    verdict is reached, and the FOLLOWING tick's ordinary worker-exit handling
    replaces the worker, recording the non-zero child exit the dead-encoder path
    records.
    """
    fixture = _guard_fixture(tmp_path)
    probe = _RecordingProbe([(0.0, 2.5)] * 3 + [(0.0, 0.0)] * 20)
    fixture.daemon._output_av_probe = probe

    assert fixture.daemon.process_once("gov") == 1
    assert len(fixture.workers) == 1

    for _ in range(_OUTPUT_AV_GUARD_CONSECUTIVE_PROBES):
        fixture.clock.value += _OUTPUT_AV_GUARD_PROBE_INTERVAL_S
        fixture.daemon.process_once("gov")

    assert fixture.workers[0].terminated
    assert fixture.workers[0].poll() == 1
    assert fixture.daemon._processes.get("gov") is fixture.workers[0]

    # The next ordinary tick notices the exit and relaunches the worker.
    fixture.clock.value += 2.0
    fixture.daemon.process_once("gov")

    assert len(fixture.workers) == 2
    assert fixture.workers[1].pid != fixture.workers[0].pid
    assert fixture.daemon._processes.get("gov") is fixture.workers[1]

    events = fixture.store.recent_proof_events("gov", 20)
    assert any(event.source_path == "ffmpeg-child:nonzero-exit" for event in events), [
        event.source_path for event in events
    ]


# --- the real probe, against real MPEG-TS ----------------------------------------


def _render_ts(path: Path, *, audio_offset_s: float) -> None:
    """One real 2s-program MPEG-TS, with the audio optionally arriving late.

    ``-itsoffset`` shifts the NEXT input's timestamps, so it is placed before
    the audio input; the video input's own timestamps are left alone. mpeg2video
    + mp2 mirror this repo's other real-ffmpeg fixtures.
    """
    audio_args = ["-itsoffset", f"{audio_offset_s}"] if audio_offset_s else []
    rendered = run_ffmpeg(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=25:d=2",
            *audio_args,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v",
            "mpeg2video",
            "-c:a",
            "mp2",
            "-t",
            "4",
            "-f",
            "mpegts",
            str(path),
        ]
    )
    assert rendered.returncode == 0, rendered.stderr


def test_u16_segment_first_packet_pts_is_none_for_a_missing_segment(tmp_path: Path) -> None:
    """Tri-state by presence, exactly like ``_segment_stream_kinds``: a segment
    that is not on disk is "no answer", not a fault and not a measurement."""
    assert _segment_first_packet_pts(tmp_path / "absent.ts") is None


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="the real probe proof needs ffmpeg and ffprobe on PATH",
)
def test_u16_segment_first_packet_pts_measures_a_real_offset(tmp_path: Path) -> None:
    """The measurement itself, against real MPEG-TS: an in-sync program reads
    ~0, and a program whose audio arrives 2s late reads ~2. A one-kind segment
    is no measurement at all (the caller measures an offset BETWEEN them)."""
    in_sync = tmp_path / "in-sync.ts"
    audio_late = tmp_path / "audio-late.ts"
    audio_only = tmp_path / "audio-only.ts"
    _render_ts(in_sync, audio_offset_s=0.0)
    _render_ts(audio_late, audio_offset_s=2.0)
    rendered = run_ffmpeg(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:a",
            "mp2",
            "-t",
            "2",
            "-f",
            "mpegts",
            str(audio_only),
        ]
    )
    assert rendered.returncode == 0, rendered.stderr

    synced = _segment_first_packet_pts(in_sync)
    assert synced is not None
    assert abs(synced[1] - synced[0]) <= 0.5, synced

    late = _segment_first_packet_pts(audio_late)
    assert late is not None
    assert 1.5 <= (late[1] - late[0]) <= 2.5, late

    assert _segment_first_packet_pts(audio_only) is None


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="the end-to-end guard proof needs ffmpeg and ffprobe on PATH",
)
def test_u16_guard_end_to_end_reads_the_complete_segment_with_real_ffprobe(
    tmp_path: Path,
) -> None:
    """The whole guard, with the daemon's OWN probe (nothing injected): the real
    playlist is read, the still-open entry is skipped, the real complete segment
    is measured with the real ffprobe, and three such probes restart the worker.

    The desynced program is deliberately the COMPLETE (second-to-last) entry, so
    a guard that measured the in-flight entry would measure the in-sync one and
    not restart -- this distinguishes the two.
    """
    hls_dir = tmp_path / "gov-live"
    media_dir = tmp_path / "source-segments"
    media_dir.mkdir()
    _render_ts(media_dir / "seg000000001.ts", audio_offset_s=2.0)  # complete: desynced
    _render_ts(media_dir / "seg000000002.ts", audio_offset_s=0.0)  # in flight: in sync

    fixture = _guard_fixture(
        tmp_path, hls_dir=hls_dir, segments=("seg000000001.ts", "seg000000002.ts")
    )

    assert fixture.daemon.process_once("gov") == 1
    # Starting a fresh relay deliberately clears any old live window. Restore
    # this test's already-rendered media after startup so the daemon probes the
    # same post-start files an active relay would have written.
    for name in ("seg000000001.ts", "seg000000002.ts"):
        shutil.copyfile(media_dir / name, hls_dir / name)
    _write_playlist(hls_dir, segments=("seg000000001.ts", "seg000000002.ts"))
    for _ in range(_OUTPUT_AV_GUARD_CONSECUTIVE_PROBES):
        fixture.clock.value += _OUTPUT_AV_GUARD_PROBE_INTERVAL_S
        fixture.daemon.process_once("gov")

    assert fixture.workers[0].terminated
