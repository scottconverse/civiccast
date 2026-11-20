# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""U31 — a relay whose UDP port is held by an ORPHANED relay ffmpeg must reclaim it.

LIVE EVIDENCE (station, 2026-09-25, ``supervisor.log`` 07:20:32-07:32:15 and
``C:\\ProgramData\\CivicCast\\data\\egress\\public\\logs\\hls-relay.Web_preview_HLS.stderr.log.1``):
the supervisor killed an unready control plane; the three relay ffmpegs it had
spawned kept running, holding the relays' loopback UDP ports. Every relay the
supervisor spawned afterwards died immediately with

    [udp @ 000001c56c24ac00] bind failed: Error number -10048 occurred

(``-10048`` = ``WSAEADDRINUSE``) and retried on the 60 s backoff forever, so all
three channels stayed off air until the orphans were killed by hand.

The primary fix is :mod:`civiccast.platform.child_containment` (the orphans stop
being created). This file pins the DEFENCE IN DEPTH for a port already held by a
process that will never release it: the supervisor reads the failed child's own
log, finds the port's owner, and kills it -- but only when that owner is an
ffmpeg whose command line names THIS relay's UDP input, and only once.

Three layers, deliberately:

* :mod:`civiccast.egress.relay_reclaim`'s control logic, pure, with a real
  station log line as the marker fixture;
* the supervisor wiring, with a fake ``PortOwnerApi`` that records every probe
  and every kill (so "never kill anything else" is asserted, not assumed), and
  a latch/backoff schedule that proves the reclaim cannot become a spawn storm;
* REAL processes: a real ffmpeg bound to a real UDP port, identified through the
  real ``psutil`` connection table and killed through the real
  ``verify_and_kill_process``; and a real NON-ffmpeg process that binds the same
  port and carries the same URI in its argv, which must be refused.

``tests/egress/test_hls_relay.py`` owns the wiring/idempotency contract,
``test_hls_relay_video_lock.py`` the stream-restore cadence, and
``test_hls_relay_stderr_log.py`` the U24 log-capture contract; none of those is
duplicated here.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from civiccast.egress.hls_relay import (
    _UDP_INPUT_ARGS,
    HlsRelaySupervisor,
    _Relay,
    hls_relay_uri_for,
)
from civiccast.egress.models import EgressConfig, EgressSinkSpec
from civiccast.egress.relay_reclaim import (
    PortOwner,
    PsutilPortOwnerApi,
    decide_reclaim,
    relay_log_shows_bind_failure,
    udp_port_of,
)
from civiccast.stream._ffmpeg import FfmpegProcessHandle, start_ffmpeg

#: The station's own words, copied byte-for-byte from the relay log quoted in
#: the module docstring. Every marker test uses THIS line rather than a
#: convenient invention, so a marker that stops matching the product ffmpeg's
#: output fails here instead of on a station.
_STATION_BIND_FAILURE_LINE = (
    b"[udp @ 000001c56c24ac00] bind failed: Error number -10048 occurred\n"
    b"[in#0 @ 000001c56c1b1940] Error opening input: Error number -10048 occurred\n"
)

_FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None,
    reason="ffmpeg is not on PATH -- the real-port-owner identification is UNPROVEN "
    "here; the fake PortOwnerApi is not a substitute for the OS connection table",
)


# ---------------------------------------------------------------------------
# relay_reclaim: reading the child's own log for the bind failure
# ---------------------------------------------------------------------------


def test_relay_log_shows_bind_failure_matches_the_station_line(tmp_path: Path) -> None:
    log = tmp_path / "hls-relay.Web.stderr.log"
    log.write_bytes(
        b"[hls-relay] pid=0000024544 utc=2026-09-25T13:30:58Z spawn=8 reason=stream-restore\n"
        + _STATION_BIND_FAILURE_LINE
    )

    assert relay_log_shows_bind_failure(log) is True


def test_a_healthy_relay_banner_is_not_a_bind_failure(tmp_path: Path) -> None:
    """The positive/negative control: real ffmpeg banner text carries no marker,
    so an ordinary relay death is never mistaken for a port conflict."""
    log = tmp_path / "hls-relay.Web.stderr.log"
    log.write_bytes(
        b"[hls-relay] pid=0000024544 utc=2026-09-25T13:30:58Z spawn=8 reason=start\n"
        b"ffmpeg version n8.1.2-50-g1a748fe2cd Copyright (c) 2000-2026 the FFmpeg developers\n"
        b"Stream mapping:\n  Stream #0:0 -> #0:0 (copy)\n"
    )

    assert relay_log_shows_bind_failure(log) is False


def test_a_bind_failure_is_looked_for_in_the_tail_not_the_head(tmp_path: Path) -> None:
    """The read is bounded to the tail (the file can be a 5 MiB storming child),
    so the marker must be found where a failing child actually writes it -- and
    a marker that has been trimmed away must NOT be resurrected from the head."""
    log = tmp_path / "hls-relay.Web.stderr.log"
    log.write_bytes(_STATION_BIND_FAILURE_LINE + b"x" * 4096)

    assert relay_log_shows_bind_failure(log, tail_bytes=1024) is False, (
        "the marker sits outside the tail window and must not be reported"
    )
    assert relay_log_shows_bind_failure(log, tail_bytes=8192) is True


@pytest.mark.parametrize("missing", ["file", "none", "directory"])
def test_relay_log_probe_fails_closed(tmp_path: Path, missing: str) -> None:
    """Every unreadable case answers "no evidence": the consequence of a false
    negative is the ordinary retry cadence, and of a false positive a kill on
    evidence we do not have."""
    if missing == "file":
        path: Path | None = tmp_path / "absent.stderr.log"
    elif missing == "directory":
        path = tmp_path
    else:
        path = None

    assert relay_log_shows_bind_failure(path) is False


def test_udp_port_of_parses_only_a_udp_relay_uri() -> None:
    assert udp_port_of("udp://127.0.0.1:18419") == 18419
    assert udp_port_of("udp://127.0.0.1:18419?overrun_nonfatal=1") == 18419
    assert udp_port_of("udp://127.0.0.1") is None
    assert udp_port_of("tcp://127.0.0.1:18419") is None
    assert udp_port_of("udp://127.0.0.1:not-a-port") is None
    assert udp_port_of("not a uri at all") is None


# ---------------------------------------------------------------------------
# relay_reclaim: the verdict (pure) -- never kill anything else
# ---------------------------------------------------------------------------


def _owner(
    *,
    pid: int = 27628,
    name: str = "ffmpeg.exe",
    exe: str = r"C:\Program Files\CivicCast (Native)\tools\ffmpeg\bin\ffmpeg.exe",
    relay_uri: str = "udp://127.0.0.1:18419",
    tokens: tuple[str, ...] | None = None,
    created_at: float = 1_700_000_000.0,
) -> PortOwner:
    if tokens is None:
        tokens = ("-i", f"{relay_uri}?overrun_nonfatal=1&fifo_size=50000000")
    return PortOwner(pid=pid, name=name, exe=exe, cmdline=tokens, created_at=created_at)


def test_the_orphan_the_station_actually_had_is_reclaimed() -> None:
    """The live shape: an ffmpeg whose argv carries this relay's URI with
    ffmpeg's UDP options appended (that is how ``_start_relay_locked`` spawns
    it)."""
    verdict = decide_reclaim(_owner(), relay_uri="udp://127.0.0.1:18419")

    assert verdict.reclaim is True
    assert "27628" in verdict.reason


def test_a_bare_uri_token_is_also_this_relay() -> None:
    """A relay-adjacent spawn may pass the URI without the options suffix; the
    identity is the URI itself, so that form matches too."""
    owner = _owner(tokens=("-i", "udp://127.0.0.1:18419"))

    assert decide_reclaim(owner, relay_uri="udp://127.0.0.1:18419").reclaim is True


def test_no_port_owner_is_a_refusal() -> None:
    verdict = decide_reclaim(None, relay_uri="udp://127.0.0.1:18419")

    assert verdict.reclaim is False
    assert "nothing in the UDP connection table" in verdict.reason


def test_a_non_ffmpeg_holding_the_port_is_refused() -> None:
    """The safety-critical refusal. A station's port can be held by anything
    (another product, a stale dev server); only an ffmpeg may ever be killed."""
    owner = _owner(name="nginx.exe", exe=r"C:\nginx\nginx.exe")

    verdict = decide_reclaim(owner, relay_uri="udp://127.0.0.1:18419")

    assert verdict.reclaim is False
    assert "27628" in verdict.reason
    assert "not an ffmpeg" in verdict.reason


def test_another_processs_ffmpeg_on_the_same_port_is_refused() -> None:
    """An ffmpeg, but not THIS relay's: its argv names a different URI. Two
    channels' sinks can collide on one port (``_PORT_RANGE`` is 500), and the
    wrong one must not be reaped to make room."""
    owner = _owner(tokens=("-i", "udp://127.0.0.1:19999?overrun_nonfatal=1"))

    verdict = decide_reclaim(owner, relay_uri="udp://127.0.0.1:18419")

    assert verdict.reclaim is False
    assert "does not name" in verdict.reason


def test_an_ffmpeg_this_supervisor_still_runs_is_refused() -> None:
    """The port collision case, from the other side: if our own live relay holds
    the port, the bind failure is a port collision to report -- killing our own
    healthy child would only move the outage."""
    owner = _owner(pid=501)

    verdict = decide_reclaim(owner, relay_uri="udp://127.0.0.1:18419", excluded_pids={501})

    assert verdict.reclaim is False
    assert "port collision" in verdict.reason


def test_a_non_pid_owner_is_refused() -> None:
    verdict = decide_reclaim(_owner(pid=0), relay_uri="udp://127.0.0.1:18419")

    assert verdict.reclaim is False
    assert "non-pid" in verdict.reason


# ---------------------------------------------------------------------------
# supervisor wiring: the EXITED branch reclaims before it restarts
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, *, pid: int = 500, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self, *, grace_seconds: float = 5.0) -> int | None:
        self.terminated = True
        self.returncode = 0
        return 0


class _FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _FakePortOwnerApi:
    """Records every probe and every kill. ``owner`` is what the OS table would
    report for ANY port, which is enough: the supervisor only ever asks about
    one port at a time and the verdict is pure."""

    def __init__(self, owner: PortOwner | None = None, *, kill_result: bool = True) -> None:
        self.owner = owner
        self.kill_result = kill_result
        self.probes: list[int] = []
        self.killed: list[PortOwner] = []

    def find_owner(self, port: int) -> PortOwner | None:
        self.probes.append(port)
        return self.owner

    def kill(self, owner: PortOwner) -> bool:
        self.killed.append(owner)
        return self.kill_result


def _config(*sinks: EgressSinkSpec, channel_id: str = "gov") -> EgressConfig:
    return EgressConfig(
        channel_id=channel_id, enabled=True, slate_message="slate", sinks=list(sinks)
    )


def _hls_sink(uri: str, label: str = "Web") -> EgressSinkSpec:
    return EgressSinkSpec(kind="hls", label=label, uri=uri)


_SENTINEL = object()


def _supervisor(
    *,
    clock: _FakeClock,
    log_root: Path | None = None,
    port_owner: _FakePortOwnerApi | None = None,
    retry_delay_s: float = 5.0,
    slow_delay_s: float = 60.0,
) -> tuple[HlsRelaySupervisor, list[list[str]], list[_FakeProcess]]:
    calls: list[list[str]] = []
    procs: list[_FakeProcess] = []

    def starter(args: list[str], *, stderr_path: Path | None = None) -> _FakeProcess:
        calls.append(args)
        procs.append(_FakeProcess(pid=500 + len(procs)))
        return procs[-1]

    sup = HlsRelaySupervisor(
        starter=starter,
        log_root=log_root,
        port_owner=port_owner,
        stream_retry_delay_s=retry_delay_s,
        stream_retry_slow_delay_s=slow_delay_s,
    )
    sup._clock = clock
    return sup, calls, procs


def _only_relay(sup: HlsRelaySupervisor) -> _Relay:
    return next(iter(sup._relays.values()))


def _write_bind_failure(sup: HlsRelaySupervisor, *, channel_id: str, sink_label: str) -> Path:
    """Append the station's bind-failure line to the child's own U24 log, the way
    the dead child wrote it."""
    path = sup._relay_log_path(channel_id, sink_label)
    assert path is not None
    with path.open("ab") as handle:
        handle.write(_STATION_BIND_FAILURE_LINE)
    return path


def test_a_bind_failed_child_reaps_the_orphan_holding_its_port(tmp_path: Path) -> None:
    """The fix, end to end at the supervisor layer: a child that died with
    ``-10048`` in its log, and an ffmpeg owning its port, must be reaped and the
    relay restarted NOW -- not left dead on the 60 s cadence."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    relay_uri = hls_relay_uri_for(sink.uri)
    orphan = _owner(pid=27628, relay_uri=relay_uri)
    api = _FakePortOwnerApi(orphan)
    sup, calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))
    procs[0].returncode = 1  # ffmpeg exited at once: the port was taken
    _write_bind_failure(sup, channel_id="gov", sink_label="Web")

    assert (
        sup.maybe_restore_missing_streams("gov", now=1000.0, producing=True, startup_grace_s=20.0)
        is True
    )
    assert api.probes == [udp_port_of(relay_uri)], "the port looked up must be this relay's"
    assert [owner.pid for owner in api.killed] == [27628]
    assert len(calls) == 2, "the relay must be restarted after the reclaim"
    assert procs[0].terminated


def test_the_reclaim_logs_a_warning_naming_the_killed_pid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An operator reading the station's log has to be able to see which process
    was killed and why -- an unexplained kill is indistinguishable from a
    crash."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    relay_uri = hls_relay_uri_for(sink.uri)
    api = _FakePortOwnerApi(_owner(pid=27628, relay_uri=relay_uri))
    sup, _calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))
    procs[0].returncode = 1
    _write_bind_failure(sup, channel_id="gov", sink_label="Web")

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.hls_relay"):
        sup.maybe_restore_missing_streams("gov", now=1000.0, producing=True, startup_grace_s=20.0)

    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    reaped = [message for message in warnings if "reaped orphaned relay ffmpeg" in message]
    assert len(reaped) == 1, warnings
    assert "27628" in reaped[0]
    assert relay_uri in reaped[0]


def test_a_dead_child_without_a_bind_failure_is_never_probed(tmp_path: Path) -> None:
    """No marker in the log means no port probe at all: an ordinary relay death
    (a crashed child, a disk fault) must not go looking for someone to kill."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    api = _FakePortOwnerApi(_owner(relay_uri=hls_relay_uri_for(sink.uri)))
    sup, calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))
    procs[0].returncode = 1  # dead, but its log shows no bind failure

    assert (
        sup.maybe_restore_missing_streams("gov", now=1000.0, producing=True, startup_grace_s=20.0)
        is True
    )
    assert api.probes == []
    assert api.killed == []
    assert len(calls) == 2, "the ordinary restart still happens"


def test_the_port_is_probed_once_per_child(tmp_path: Path) -> None:
    """The one-shot latch: once this child's log has been read and the table
    consulted, a later tick must not read or probe again -- the probe is the
    expensive part and it is already answered."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    relay_uri = hls_relay_uri_for(sink.uri)
    api = _FakePortOwnerApi(_owner(pid=27628, relay_uri=relay_uri), kill_result=False)
    sup, _calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))
    procs[0].returncode = 1
    _write_bind_failure(sup, channel_id="gov", sink_label="Web")
    relay = _only_relay(sup)

    assert sup._maybe_reclaim_orphan("gov|Web", "gov", relay, now=1000.0) is False
    assert api.probes == [udp_port_of(relay_uri)]
    assert sup._maybe_reclaim_orphan("gov|Web", "gov", relay, now=1000.5) is False

    assert api.probes == [udp_port_of(relay_uri)], "the probe must not be repeated"


def test_a_kill_that_fails_falls_back_to_the_ordinary_cadence(tmp_path: Path) -> None:
    """``verify_and_kill_process`` can refuse (a recycled pid, or a process
    owned by another user). The reclaim then reports no recovery, the ordinary
    restart path still runs, and -- because the replacement inherits the retry
    window -- the next tick does not spawn again."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    api = _FakePortOwnerApi(
        _owner(pid=27628, relay_uri=hls_relay_uri_for(sink.uri)), kill_result=False
    )
    sup, calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))
    procs[0].returncode = 1
    _write_bind_failure(sup, channel_id="gov", sink_label="Web")

    assert (
        sup.maybe_restore_missing_streams("gov", now=1000.0, producing=True, startup_grace_s=20.0)
        is True
    )
    assert api.killed != []
    assert len(calls) == 2  # the ordinary restart, on the ordinary cadence

    clock.value = 1000.25  # inside the retry delay
    assert (
        sup.maybe_restore_missing_streams(
            "gov", now=clock.value, producing=True, startup_grace_s=20.0
        )
        is False
    )
    assert len(calls) == 2, "a failed reclaim must not become a per-tick spawn storm"


def test_a_live_relay_holding_the_port_is_refused_not_killed(tmp_path: Path) -> None:
    """The collision case at the supervisor layer: the port's owner is another
    relay THIS supervisor is running (two sinks hashing to one port). Nothing
    may be killed, and the dead child is still restarted on the ordinary
    cadence."""
    clock = _FakeClock(1000.0)
    sink_a = _hls_sink(str(tmp_path / "gov"))
    sink_b = _hls_sink(str(tmp_path / "edu"), label="Edu")
    api = _FakePortOwnerApi(_owner(pid=501, relay_uri=hls_relay_uri_for(sink_a.uri)))
    sup, _calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink_a, sink_b))  # procs[0] = gov's relay, procs[1] = edu's
    procs[0].returncode = 1
    _write_bind_failure(sup, channel_id="gov", sink_label="Web")

    assert (
        sup.maybe_restore_missing_streams("gov", now=1000.0, producing=True, startup_grace_s=20.0)
        is True
    )
    assert api.killed == [], "the supervisor must never kill a relay it is still running"
    assert procs[1].terminated is False


def test_reclaims_are_bounded_by_the_episode_counter(tmp_path: Path) -> None:
    """THE BOUND. A reclaim jumps the restart backoff (waiting 60 s to rebind a
    held port is the outage being fixed), so it carries its own limit: the
    episode counter is copied onto each replacement, and the reclaim stops
    bypassing once the fast attempts are spent. Ten one-second ticks with a
    refused holder must therefore produce the ordinary cadence's spawns, not
    one per tick."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    api = _FakePortOwnerApi(_owner(name="nginx.exe", exe=r"C:\nginx\nginx.exe"))
    sup, calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))

    for step in range(10):
        clock.value = 1000.0 + step
        procs[-1].returncode = 1  # every child dies on the held port
        _write_bind_failure(sup, channel_id="gov", sink_label="Web")
        sup.maybe_restore_missing_streams(
            "gov", now=clock.value, producing=True, startup_grace_s=20.0
        )

    # t=1000 spawns the first replacement, t=1005 the second (the 5s fast
    # delay); the rest are inside a retry window. Ten ticks, two spawns.
    assert len(calls) == 3, f"{len(calls)} children spawned over 10 ticks: {calls}"
    assert api.killed == [], "a non-ffmpeg holder is never killed, however often it is seen"


def test_a_refused_reclaim_names_the_reason_at_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusal an operator has to act on -- a port held by something we will
    not kill -- must say so, and name the pid."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    api = _FakePortOwnerApi(_owner(pid=4242, name="nginx.exe", exe=r"C:\nginx\nginx.exe"))
    sup, _calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))
    procs[0].returncode = 1
    _write_bind_failure(sup, channel_id="gov", sink_label="Web")

    with caplog.at_level(logging.WARNING, logger="civiccast.egress.hls_relay"):
        sup.maybe_restore_missing_streams("gov", now=1000.0, producing=True, startup_grace_s=20.0)

    refusals = [
        record.getMessage()
        for record in caplog.records
        if "may not be reaped" in record.getMessage()
    ]
    assert len(refusals) == 1, [record.getMessage() for record in caplog.records]
    assert "4242" in refusals[0]
    assert "not an ffmpeg" in refusals[0]


def test_a_reclaim_replacement_logs_its_own_spawn_reason(tmp_path: Path) -> None:
    """The replacement's U24 header must say ``orphan-reclaim``: the log is the
    only record an operator has of why a child exists, and "restore" would hide
    that a process was killed."""
    clock = _FakeClock(1000.0)
    sink = _hls_sink(str(tmp_path / "gov"))
    api = _FakePortOwnerApi(_owner(pid=27628, relay_uri=hls_relay_uri_for(sink.uri)))
    sup, _calls, procs = _supervisor(clock=clock, log_root=tmp_path, port_owner=api)
    sup.apply(_config(sink))
    procs[0].returncode = 1
    _write_bind_failure(sup, channel_id="gov", sink_label="Web")

    sup.maybe_restore_missing_streams("gov", now=1000.0, producing=True, startup_grace_s=20.0)

    path = sup._relay_log_path("gov", "Web")
    assert path is not None
    assert b"reason=orphan-reclaim" in path.read_bytes()


# ---------------------------------------------------------------------------
# REAL processes: the OS connection table, a real ffmpeg, a real kill
#
# The wiring tests above prove the supervisor's decisions given an owner. These
# prove the owner itself: that ``psutil``'s UDP table names the pid of a real
# ffmpeg bound to a real port on this box, that the kill really reaps it, and
# that a real NON-ffmpeg holding the same port -- with this relay's URI in its
# command line, so only the "is it an ffmpeg" gate can refuse it -- is refused.
# ---------------------------------------------------------------------------

#: Binds a UDP port and waits. Deliberately NOT an ffmpeg, and its command line
#: carries the relay URI, so it is the adversarial case for the file gate: a
#: verdict that matched on argv alone would kill it.
_BINDER_SRC = """
import socket, sys, time

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", int(sys.argv[1])))
print("bound", flush=True)
time.sleep(120)
"""


def _free_udp_port() -> int:
    """A UDP port nothing is using right now.

    The OS picks from the ephemeral range (49152+ on this box), far from the
    18000-18500 the station's relays use, so a test never contends with the live
    station's own ports.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_owner(
    api: PsutilPortOwnerApi, port: int, *, timeout_s: float = 20.0
) -> PortOwner | None:
    """Poll the REAL connection table until the port has an owner, or give up.

    The child binds asynchronously (ffmpeg opens the input a few hundred ms
    after spawn), so a single probe races it.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        owner = api.find_owner(port)
        if owner is not None:
            return owner
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)


def _wait_for_exit(handle: FfmpegProcessHandle, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while handle.poll() is None:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _reap(pid: int | None, *, timeout_s: float = 5.0) -> None:
    """Test-local cleanup: kill a pid this test started, and say nothing if it
    is already gone. Never used to assert product behaviour."""
    if pid is None or pid <= 0:
        return
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        process.kill()
        process.wait(timeout=timeout_s)
    except psutil.NoSuchProcess:
        pass


def _relay_shaped_argv(relay_uri: str) -> list[str]:
    """The argv ``_start_relay_locked`` builds for a UDP relay, up to the input.

    The input token is the production form, verbatim -- the identity match this
    whole module rests on is that an orphan's argv carries THIS token.
    """
    return [
        *_UDP_INPUT_ARGS,
        "-i",
        f"{relay_uri}?overrun_nonfatal=1&fifo_size=50000000",
        "-f",
        "null",
        "-",
    ]


def test_a_free_port_has_no_owner_in_the_real_table() -> None:
    """The negative control for the probe itself: ``find_owner`` does not answer
    with something for every port, so a match below means something."""
    api = PsutilPortOwnerApi()
    port = _free_udp_port()

    assert api.find_owner(port) is None


def _put_real_ffmpeg_first_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make PATH resolve to the real ffmpeg executable, not a launcher shim.

    A Scoop install puts ``shims\\ffmpeg.exe`` on PATH. That shim is a separate
    process that starts the real ``ffmpeg.exe`` as its child, so the UDP port
    owner is the child while ``start_ffmpeg`` holds the shim's pid. The product
    ships a real ``ffmpeg.exe`` on PATH, so put the real one first here too.
    """
    if _FFMPEG is None:
        return
    shim_file = Path(_FFMPEG).with_suffix(".shim")
    if not shim_file.is_file():
        return
    match = re.search(r'^path\s*=\s*"?([^"\r\n]+)"?', shim_file.read_text("utf-8"), re.MULTILINE)
    assert match is not None, f"unreadable launcher shim: {shim_file}"
    real_dir = str(Path(match.group(1)).parent)
    monkeypatch.setenv("PATH", real_dir + os.pathsep + os.environ.get("PATH", ""))


@requires_ffmpeg
def test_a_real_relay_shaped_ffmpeg_is_identified_and_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism, end to end against the real OS: a real ffmpeg bound to a
    real UDP port is found by pid through ``psutil``, accepted as this relay's
    orphan, and killed -- and the port comes free."""
    _put_real_ffmpeg_first_on_path(monkeypatch)
    port = _free_udp_port()
    relay_uri = f"udp://127.0.0.1:{port}"
    api = PsutilPortOwnerApi()
    handle = start_ffmpeg(_relay_shaped_argv(relay_uri), stderr_path=tmp_path / "ffmpeg.stderr.log")
    owner: PortOwner | None = None
    try:
        owner = _wait_for_owner(api, port)
        assert owner is not None, (
            f"ffmpeg pid {handle.pid} never showed as the owner of udp port {port}; "
            f"stderr: {(tmp_path / 'ffmpeg.stderr.log').read_text('utf-8', 'replace')!r}"
        )
        assert owner.pid == handle.pid, (
            f"the table names pid {owner.pid}, but the relay child is pid {handle.pid}"
        )

        verdict = decide_reclaim(owner, relay_uri=relay_uri)

        assert verdict.reclaim is True, verdict.reason
        assert api.kill(owner) is True, "the real kill primitive refused a real orphan"
        assert _wait_for_exit(handle, 2.0), "the reaped ffmpeg is still running after 2 s"
        assert _wait_for_owner(api, port, timeout_s=5.0) is None, (
            "the port still has an owner after the kill"
        )
    finally:
        # Unconditional: ``terminate`` also closes the stderr file this handle
        # owns, and the process may already have been reaped by the product
        # (``-W error`` makes a leaked handle a failure, not a warning).
        handle.terminate()
        _reap(owner.pid if owner is not None else None)


@requires_ffmpeg
def test_a_real_non_ffmpeg_holding_the_port_is_refused() -> None:
    """The safety-critical real case: the holder's command line NAMES this
    relay's URI (so the argv gate cannot be what refuses it) but it is a python
    process, not an ffmpeg. It must be refused -- and, because the test asserts
    a refusal rather than a kill, it is still alive to prove it."""
    port = _free_udp_port()
    relay_uri = f"udp://127.0.0.1:{port}"
    api = PsutilPortOwnerApi()
    child = subprocess.Popen(
        [sys.executable, "-c", _BINDER_SRC, str(port), relay_uri],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    owner: PortOwner | None = None
    try:
        owner = _wait_for_owner(api, port)
        assert owner is not None, (
            f"the binder (pid {child.pid}) never showed as the owner of udp port {port}"
        )
        # Asserted against the raw argv rather than through the product's own
        # matcher: this is the TEST's premise (the argv gate would pass), and a
        # premise checked with the code under test is not checked at all.
        assert any(relay_uri in token for token in owner.cmdline), (
            f"this test proves the FILE gate refuses: the holder's argv must name "
            f"{relay_uri}, but it is {owner.cmdline}"
        )

        verdict = decide_reclaim(owner, relay_uri=relay_uri)

        assert verdict.reclaim is False, "a non-ffmpeg port holder must never be killed"
        assert "not an ffmpeg" in verdict.reason, verdict.reason
        assert psutil.Process(owner.pid).is_running(), (
            "the refused holder must still be alive -- a refusal that killed it "
            "anyway is not a refusal"
        )
    finally:
        _reap(owner.pid if owner is not None else None)
        _reap(child.pid)
        # ``wait`` is what settles the Popen object itself; killing the pid
        # leaves ``returncode`` unset, and an unreaped child is a ResourceWarning
        # -- an error under ``-W error``.
        child.wait(timeout=10.0)
