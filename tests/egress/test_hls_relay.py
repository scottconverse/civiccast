# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""DEFECT A — the GStreamer engine's hls-sink relay.

Unit layer only (fake ffmpeg starter): supervisor lifecycle, config
rewriting, idempotency, graceful degradation when ffmpeg is unavailable.
See ``tests/egress/test_hls_sink_live_playability.py`` for the real-ffmpeg
proof that ``HlsSink.output_args()`` (reused here unchanged) writes a
genuinely playable manifest + segments; this file proves the relay wires
that proven muxer up correctly, not that the muxer itself works.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from civiccast.egress.hls_relay import (
    HlsRelaySupervisor,
    _claim_playlist_snapshot,
    _clear_hls_window,
    _ManifestPublisher,
    _relay_hls_output_args,
    hls_relay_uri_for,
)
from civiccast.egress.models import EgressConfig, EgressSinkSpec
from civiccast.egress.sinks import HlsSink
from civiccast.stream._ffmpeg import FfmpegNotFoundError


def test_private_mux_playlist_replacement_with_held_publisher_snapshot(tmp_path: Path) -> None:
    staging = tmp_path / "playlist.mux.m3u8"
    pending = tmp_path / "playlist.mux.m3u8.tmp"
    staging.write_bytes(b"old playlist")
    snapshot = _claim_playlist_snapshot(staging)
    with snapshot.open("rb") as reader:
        pending.write_bytes(b"new playlist")
        pending.replace(staging)
        # Exercise replacement of an existing destination too, while the
        # publisher still holds its reader. Windows MoveFileEx denied this
        # operation when that reader held staging itself, even with share=7.
        pending.write_bytes(b"newest playlist")
        pending.replace(staging)
        assert reader.read() == b"old playlist"
        assert staging.read_bytes() == b"newest playlist"
    snapshot.unlink()


def _config(*sinks: EgressSinkSpec, channel_id: str = "gov") -> EgressConfig:
    return EgressConfig(
        channel_id=channel_id, enabled=True, slate_message="slate", sinks=list(sinks)
    )


def _hls_sink(uri: str = "C:/CivicCast/live/gov", label: str = "Web") -> EgressSinkSpec:
    return EgressSinkSpec(kind="hls", label=label, uri=uri)


class _FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self, *, grace_seconds: float = 5.0) -> int | None:
        self.terminated = True
        self._returncode = 0
        return 0


def _supervisor():
    calls: list[list[str]] = []
    procs: list[_FakeProcess] = []

    def starter(args: list[str]) -> _FakeProcess:
        calls.append(args)
        proc = _FakeProcess()
        procs.append(proc)
        return proc

    return HlsRelaySupervisor(starter=starter), calls, procs


def test_hls_relay_uri_for_is_pure_and_deterministic() -> None:
    uri_a = hls_relay_uri_for("C:/CivicCast/live/gov")
    uri_b = hls_relay_uri_for("C:/CivicCast/live/gov")
    assert uri_a == uri_b
    parsed = urlsplit(uri_a)
    assert parsed.scheme == "udp"
    assert parsed.hostname == "127.0.0.1"
    assert 18_000 <= parsed.port < 18_500


def test_hls_relay_uri_for_differs_per_directory() -> None:
    a = hls_relay_uri_for("C:/CivicCast/live/gov")
    b = hls_relay_uri_for("C:/CivicCast/live/school-board")
    assert a != b


def test_apply_rewrites_hls_sink_to_local_ts_and_starts_relay() -> None:
    sup, calls, procs = _supervisor()
    config = _config(_hls_sink())

    updated = sup.apply(config)

    assert len(calls) == 1
    assert len(procs) == 1
    rewritten = updated.sinks[0]
    assert rewritten.kind == "local-ts"
    assert rewritten.uri == hls_relay_uri_for("C:/CivicCast/live/gov")
    # The original config is untouched (in-memory rewrite only, mirrors
    # TsRelaySupervisor — never persisted back to the store).
    assert config.sinks[0].kind == "hls"


def test_apply_feeds_the_relay_the_real_hls_muxer_args() -> None:
    """The relay child's args must be exactly ``HlsSink(original_spec).output_args()``
    appended to a udp input — the same, already-proven ffmpeg HLS invocation the
    legacy engine uses, not a reinvented one."""
    from civiccast.egress.sinks import HlsSink

    sup, calls, _procs = _supervisor()
    sink = _hls_sink()
    sup.apply(_config(sink))

    args = calls[0]
    assert "-i" in args
    input_uri = args[args.index("-i") + 1]
    assert input_uri.startswith(hls_relay_uri_for(sink.uri))
    expected_output_args = ["-copyts", *HlsSink(sink).output_args()]
    expected_output_args[-1:-1] = ["-hls_segment_options", "mpegts_copyts=1"]
    flags_index = expected_output_args.index("-hls_flags") + 1
    expected_output_args[flags_index] = "+".join(
        flag for flag in expected_output_args[flags_index].split("+") if flag != "delete_segments"
    )
    expected_output_args[flags_index] += "+temp_file"
    assert args[-len(expected_output_args) :] == expected_output_args


def test_relay_leaves_segment_deletion_to_the_manifest_publisher() -> None:
    sup, calls, _procs = _supervisor()
    sup.apply(_config(_hls_sink()))

    args = calls[0]
    flags = args[args.index("-hls_flags") + 1].split("+")
    assert "delete_segments" not in flags
    assert "temp_file" in flags


def _playlist(entries: list[tuple[float, str]]) -> bytes:
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:8", "#EXT-X-MEDIA-SEQUENCE:1"]
    for duration, name in entries:
        lines.extend((f"#EXTINF:{duration},", name))
    return ("\n".join(lines) + "\n").encode("utf-8")


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_publisher_retains_retired_segments_for_the_previous_playlist_lifetime(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "hls"
    directory.mkdir()
    manifest = directory / "playlist.m3u8"
    staging = directory / "playlist.mux.m3u8"
    retired = "seg000000001.ts"
    shared = "seg000000002.ts"
    newest = "seg000000003.ts"
    for name in (retired, shared, newest):
        (directory / name).write_bytes(b"segment")
    manifest.write_bytes(_playlist([(7.25, retired), (1.5, shared)]))
    clock = _Clock()
    publisher = _ManifestPublisher(manifest_path=manifest, staging_path=staging, clock=clock)

    staging.write_bytes(_playlist([(1.5, shared), (1.25, newest)]))
    publisher._publish_once()
    assert retired not in manifest.read_text(encoding="utf-8")
    assert (directory / retired).exists()

    # The previous published playlist lasted 8.75s and its retired segment
    # lasted 7.25s. A client may still be using that playlist for their sum.
    clock.now += 15.99
    publisher._publish_once()
    assert (directory / retired).exists()

    clock.now += 0.02
    publisher._publish_once()
    assert not (directory / retired).exists()
    assert (directory / shared).exists()
    assert (directory / newest).exists()


def test_publisher_pruning_protects_unpublished_new_segments_and_exact_names(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "hls"
    directory.mkdir()
    manifest = directory / "playlist.m3u8"
    staging = directory / "playlist.mux.m3u8"
    active = "seg000000010.ts"
    old = "seg000000009.ts"
    ahead = "seg000000011.ts"
    malformed = "seg-not-a-segment.ts"
    for name in (active, old, ahead, malformed):
        (directory / name).write_bytes(b"segment")
    window = _playlist([(2.0, active)])
    manifest.write_bytes(window)
    staging.write_bytes(window)
    clock = _Clock()
    publisher = _ManifestPublisher(manifest_path=manifest, staging_path=staging, clock=clock)

    # The old orphan gets one observed-window grace. Even after expiry, a
    # segment newer than the staging playlist can be in the muxer's
    # rename-before-playlist-update interval, so it remains protected.
    clock.now += 4.01
    publisher._publish_once()

    assert not (directory / old).exists()
    assert (directory / ahead).exists()
    assert (directory / malformed).exists()
    assert (directory / active).exists()


def test_publisher_does_not_publish_a_playlist_with_a_missing_segment(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "hls"
    directory.mkdir()
    manifest = directory / "playlist.m3u8"
    staging = directory / "playlist.mux.m3u8"
    existing = "seg000000001.ts"
    (directory / existing).write_bytes(b"segment")
    original = _playlist([(2.0, existing)])
    manifest.write_bytes(original)
    publisher = _ManifestPublisher(manifest_path=manifest, staging_path=staging)

    staging.write_bytes(_playlist([(2.0, "seg000000002.ts")]))
    publisher._publish_once()

    assert manifest.read_bytes() == original

    # A delayed segment can arrive after staging has been consumed, with no
    # further muxer window. Retry the retained bytes without a private file.
    assert not staging.exists()
    (directory / "seg000000002.ts").write_bytes(b"late segment")
    publisher._publish_once()
    assert manifest.read_bytes() == _playlist([(2.0, "seg000000002.ts")])


def test_publisher_retries_public_write_without_a_new_private_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "playlist.m3u8"
    staging = tmp_path / "playlist.mux.m3u8"
    old_segment = "seg000000001.ts"
    (tmp_path / old_segment).write_bytes(b"old segment")
    original = _playlist([(2.0, old_segment)])
    manifest.write_bytes(original)
    segment = "seg000000002.ts"
    (tmp_path / segment).write_bytes(b"segment")
    publisher = _ManifestPublisher(manifest_path=manifest, staging_path=staging)
    window = _playlist([(2.0, segment)])
    staging.write_bytes(window)
    write_manifest = publisher._write_manifest
    monkeypatch.setattr(publisher, "_write_manifest", lambda _data: False)

    publisher._publish_once()
    assert manifest.read_bytes() == original
    assert not staging.exists()

    monkeypatch.setattr(publisher, "_write_manifest", write_manifest)
    publisher._publish_once()
    assert manifest.read_bytes() == window
    assert not list(tmp_path.glob("*.snapshot"))


@pytest.mark.parametrize("pending", [b"partial playlist", _playlist([(2.0, "seg000000003.ts")])])
def test_publisher_invalid_pending_preserves_publication_and_segments(
    tmp_path: Path,
    pending: bytes,
) -> None:
    manifest = tmp_path / "playlist.m3u8"
    staging = tmp_path / "playlist.mux.m3u8"
    old = "seg000000001.ts"
    active = "seg000000002.ts"
    ahead = "seg000000004.ts"
    for name in (old, active, ahead):
        (tmp_path / name).write_bytes(b"segment")
    working = _playlist([(2.0, active)])
    manifest.write_bytes(working)
    staging.write_bytes(working)
    clock = _Clock()
    publisher = _ManifestPublisher(manifest_path=manifest, staging_path=staging, clock=clock)
    staging.write_bytes(pending)

    clock.now += 10.0
    publisher._publish_once()
    publisher._publish_once()  # No new private file, still must not prune.
    assert manifest.read_bytes() == working
    assert (tmp_path / old).exists()
    assert (tmp_path / ahead).exists()
    assert not staging.exists()

    # A later complete window supersedes the invalid snapshot.
    replacement = _playlist([(2.0, ahead)])
    staging.write_bytes(replacement)
    publisher._publish_once()
    assert manifest.read_bytes() == replacement


@pytest.mark.parametrize("initial", [False, True])
def test_publisher_retries_owned_snapshot_read_without_advertising_initial_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial: bool,
) -> None:
    manifest = tmp_path / "playlist.m3u8"
    staging = tmp_path / "playlist.mux.m3u8"
    segment = "seg000000001.ts"
    (tmp_path / segment).write_bytes(b"segment")
    window = _playlist([(2.0, segment)])
    if initial:
        staging.write_bytes(window)
    read_bytes = Path.read_bytes

    def blocked_snapshot_read(path: Path) -> bytes:
        if path.suffix == ".snapshot":
            raise PermissionError("busy snapshot")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", blocked_snapshot_read)
    publisher = _ManifestPublisher(manifest_path=manifest, staging_path=staging)
    if not initial:
        staging.write_bytes(window)
        publisher._publish_once()
    assert not staging.exists()
    assert len(list(tmp_path.glob("*.snapshot"))) == 1

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    publisher._publish_once()
    if initial:
        assert not manifest.exists()  # Prior-session windows stay unadvertised.
    else:
        assert manifest.read_bytes() == window
    assert not list(tmp_path.glob("*.snapshot"))

    staging.write_bytes(window)
    publisher._publish_once()
    assert manifest.read_bytes() == window


def test_fresh_restart_withdraws_old_playlist_and_retains_its_segments_until_expiry(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "hls"
    directory.mkdir()
    spec = _hls_sink(uri=str(directory))
    sink = HlsSink(spec)
    manifest = Path(sink.manifest_target())
    staging = Path(sink.connect_target())
    retired = "seg000000001.ts"
    shared = "seg000000002.ts"
    orphan = "seg000000000.ts"
    for name in (retired, shared, orphan):
        (directory / name).write_bytes(name.encode())
    previous = _playlist([(7.25, retired), (1.5, shared)])
    manifest.write_bytes(previous)

    clear_result = _clear_hls_window(sink)

    assert not manifest.exists()
    assert (directory / retired).read_bytes() == retired.encode()
    assert (directory / shared).read_bytes() == shared.encode()
    assert (directory / orphan).read_bytes() == orphan.encode()
    assert clear_result.published_segments == {retired: 7.25, shared: 1.5}
    assert clear_result.retired_segment_names == frozenset({orphan})
    assert clear_result.retired_grace_s == 16.0
    assert clear_result.next_start_number == 3

    output_args = _relay_hls_output_args(sink, start_number=clear_result.next_start_number)
    assert output_args[output_args.index("-start_number") + 1] == "3"

    clock = _Clock()
    publisher = _ManifestPublisher(
        manifest_path=manifest,
        staging_path=staging,
        clock=clock,
        previous_window=clear_result,
    )
    new_segment = "seg000000003.ts"
    (directory / new_segment).write_bytes(b"new segment")
    next_window = _playlist([(1.5, new_segment)])
    staging.write_bytes(next_window)
    publisher._publish_once()

    assert manifest.read_bytes() == next_window
    assert (directory / retired).read_bytes() == retired.encode()
    assert (directory / new_segment).read_bytes() == b"new segment"

    # The previous playlist lasted 8.75s and its oldest segment lasted 7.25s.
    clock.now = 115.99
    publisher._publish_once()
    assert (directory / retired).exists()
    assert (directory / orphan).exists()
    clock.now = 116.01
    publisher._publish_once()
    assert not (directory / retired).exists()
    assert not (directory / shared).exists()
    assert not (directory / orphan).exists()


def test_apply_is_idempotent_for_an_unchanged_sink() -> None:
    sup, calls, procs = _supervisor()
    config = _config(_hls_sink())

    sup.apply(config)
    sup.apply(config)

    assert len(calls) == 1  # relay reused, not restarted
    assert not procs[0].terminated


def test_apply_restarts_the_relay_when_the_directory_changes() -> None:
    sup, calls, procs = _supervisor()
    sup.apply(_config(_hls_sink(uri="C:/CivicCast/live/gov")))
    sup.apply(_config(_hls_sink(uri="C:/CivicCast/live/gov-v2")))

    assert len(calls) == 2
    assert procs[0].terminated  # the stale relay was torn down
    assert not procs[1].terminated


def test_apply_passes_through_configs_without_an_hls_sink() -> None:
    sup, calls, _procs = _supervisor()
    config = _config(EgressSinkSpec(kind="udp-ts", label="head", uri="udp://10.0.0.9:5000"))

    updated = sup.apply(config)

    assert updated is config  # untouched, same instance
    assert calls == []


def test_apply_degrades_gracefully_when_ffmpeg_is_unavailable() -> None:
    """No ffmpeg on PATH must not crash the channel: the sink is returned
    unchanged (still hls) so sink_element_spec's own fallback branch builds
    a real, if unreceived, udpsink — degraded (no live HLS), never a crash."""

    def starter(_args: list[str]) -> _FakeProcess:
        raise FfmpegNotFoundError("no ffmpeg on PATH")

    sup = HlsRelaySupervisor(starter=starter)
    config = _config(_hls_sink())

    updated = sup.apply(config)

    assert updated is config
    assert updated.sinks[0].kind == "hls"


def test_stop_channel_terminates_only_that_channels_relays() -> None:
    sup, _calls, procs = _supervisor()
    sup.apply(_config(_hls_sink(), channel_id="gov"))
    sup.apply(_config(_hls_sink(uri="C:/CivicCast/live/other"), channel_id="other"))

    sup.stop_channel("gov")

    assert procs[0].terminated
    assert not procs[1].terminated


def test_stop_all_terminates_every_relay() -> None:
    sup, _calls, procs = _supervisor()
    sup.apply(_config(_hls_sink(), channel_id="gov"))
    sup.apply(_config(_hls_sink(uri="C:/CivicCast/live/other"), channel_id="other"))

    sup.stop_all()

    assert all(proc.terminated for proc in procs)


def test_apply_creates_a_fresh_relay_after_stop_channel() -> None:
    sup, calls, procs = _supervisor()
    config = _config(_hls_sink())
    sup.apply(config)
    sup.stop_channel("gov")

    sup.apply(config)

    assert len(calls) == 2
    assert procs[0].terminated
    assert not procs[1].terminated


# --- U21: a new worker session must not inherit the old relay's timeline ---------------
#
# The relay child does its own per-stream PTS-discontinuity correction and keeps
# the resulting offset for as long as the child lives (U21 task A, proven on the
# station's bundled ffmpeg: a one-sided +20s forward jump on the RE-ENCODED audio
# leg left a permanent -10.0s output a-v offset that survived every later
# segment, and the relay logged a DIFFERENT "new offset" per stream). So reuse is
# only safe while the writer the relay serves is the same writer. Every genuine
# worker (re)start -- first start, crash relaunch, output-desync guard restart,
# slate->program restart -- must rebind the channel's relay to the new session.


def test_apply_with_new_session_rebinds_the_relay_to_the_new_worker_session() -> None:
    sup, calls, procs = _supervisor()
    config = _config(_hls_sink())

    first = sup.apply(config)
    second = sup.apply(config, new_session=True)

    assert len(calls) == 2, "the new worker session kept the previous session's relay child"
    assert procs[0].terminated, "the previous session's relay child was left running"
    assert not procs[1].terminated
    # Same argv, therefore the same deterministic udp port: the config already
    # handed to the engine graph stays valid across the rebind.
    assert calls[0] == calls[1]
    assert second.sinks[0].uri == first.sinks[0].uri == hls_relay_uri_for("C:/CivicCast/live/gov")


def test_apply_without_new_session_keeps_the_relay_for_an_in_place_reload() -> None:
    """The reload path (``new_session=False``) is not a worker restart: the
    relay must be left alone so an in-place reload keeps one continuous mux."""

    sup, calls, procs = _supervisor()
    config = _config(_hls_sink())

    sup.apply(config)
    sup.apply(config, new_session=False)

    assert len(calls) == 1
    assert not procs[0].terminated


def test_new_session_withdraws_fresh_or_dead_playlist_but_preserves_referenced_segments(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "live"
    directory.mkdir()
    config = _config(_hls_sink(uri=str(directory)))
    playlist = directory / "playlist.m3u8"
    segment = directory / "seg000000001.ts"
    sup, calls, procs = _supervisor()

    # A fresh relay withdraws the old playlist first but retains the segment
    # its already-served bytes may still reference. The replacement starts at
    # a distinct segment number.
    playlist.write_bytes(_playlist([(2.0, segment.name)]))
    segment.write_bytes(b"stale segment")
    sup.apply(config)
    assert not playlist.exists()
    assert segment.read_bytes() == b"stale segment"
    assert calls[0][calls[0].index("-start_number") + 1] == "2"

    # A live relay's window belongs to the same broadcast during a worker
    # crash-relaunch and stays visible while its relay child is rebound.
    playlist.write_text("#EXTM3U\n#live\n", encoding="utf-8")
    segment.write_bytes(b"live segment")
    sup.apply(config, new_session=True)
    assert procs[0].terminated
    assert playlist.is_file()
    assert segment.is_file()

    # A same-label sink redirected to another directory is not the same
    # output window. Its manifest is withdrawn while referenced files stay
    # available to clients holding snapshots.
    directory = tmp_path / "redirected"
    directory.mkdir()
    config = _config(_hls_sink(uri=str(directory)))
    playlist = directory / "playlist.m3u8"
    segment = directory / "seg000000001.ts"
    playlist.write_bytes(_playlist([(2.0, segment.name)]))
    segment.write_bytes(b"stale segment")
    sup.apply(config, new_session=True)
    assert procs[1].terminated
    assert not playlist.exists()
    assert segment.read_bytes() == b"stale segment"

    # A dead relay cannot prove that the old window is still live. Withdraw
    # its playlist before starting the replacement, retaining its references
    # through the previous window's grace period.
    playlist.write_bytes(_playlist([(2.0, segment.name)]))
    segment.write_bytes(b"stale segment")
    procs[2]._returncode = 1
    sup.apply(config, new_session=True)
    assert not playlist.exists()
    assert segment.read_bytes() == b"stale segment"


def test_apply_with_new_session_drops_a_previous_sessions_relay_with_no_hls_sink() -> None:
    """A channel restarted onto a config with no hls sink at all must not leave
    the previous session's relay child (and its udp port) running."""

    sup, calls, procs = _supervisor()
    sup.apply(_config(_hls_sink()))
    config = _config(EgressSinkSpec(kind="file", label="Proof", uri="build/out.ts"))

    updated = sup.apply(config, new_session=True)

    assert updated is config
    assert procs[0].terminated
    assert calls == [calls[0]]  # nothing respawned
    assert sup.is_alive("gov") is None


# --- MAJOR M1: relay-child liveness ----------------------------------------------------
#
# Before ``is_alive`` existed, nothing polled the relay subprocess after ``apply()``
# started it: a relay that died later (disk full, ffmpeg missing/removed mid-run, OOM)
# stayed invisible until the channel's next full encoder start/reload happened to call
# ``apply()`` again. These tests pin the liveness contract ``civiccast.egress.daemon``
# polls every tick; see ``tests/egress/test_daemon.py`` for the daemon-level integration
# (health surfacing) proof.


def test_is_alive_is_none_when_no_relay_is_tracked_for_the_channel() -> None:
    sup, _calls, _procs = _supervisor()

    assert sup.is_alive("gov") is None


def test_is_alive_is_true_while_the_relay_process_is_running() -> None:
    sup, _calls, procs = _supervisor()
    sup.apply(_config(_hls_sink(), channel_id="gov"))

    assert sup.is_alive("gov") is True
    assert not procs[0].terminated  # is_alive is read-only — it must not touch the process


def test_is_alive_is_false_once_the_relay_process_has_exited() -> None:
    sup, _calls, procs = _supervisor()
    sup.apply(_config(_hls_sink(), channel_id="gov"))

    procs[0]._returncode = 1  # simulate the relay child dying on its own (disk full / OOM)

    assert sup.is_alive("gov") is False


def test_is_alive_only_reports_on_the_named_channels_relays() -> None:
    sup, _calls, procs = _supervisor()
    sup.apply(_config(_hls_sink(), channel_id="gov"))
    sup.apply(_config(_hls_sink(uri="C:/CivicCast/live/other"), channel_id="other"))

    procs[0]._returncode = 1  # only "gov"'s relay died

    assert sup.is_alive("gov") is False
    assert sup.is_alive("other") is True


def test_udp_input_args_use_widened_analyze_and_probe_for_delayed_video() -> None:
    """REGRESSION: a delayed-video UDP TS can lock the relay to audio-only when the
    ffmpeg start-time probe window is too small. The isolated synthetic proof shows
    2,000,000 locks to AAC-only while 6,000,000 retains H264/AAC. Pin BOTH options
    to the widened value so the two never drift apart."""
    from civiccast.egress.hls_relay import _UDP_INPUT_ARGS

    args = tuple(_UDP_INPUT_ARGS)
    # both flags must be present and identical
    assert args.count("-analyzeduration") == 1
    assert args.count("-probesize") == 1
    ad = args[args.index("-analyzeduration") + 1]
    ps = args[args.index("-probesize") + 1]
    assert ad == ps, f"analyze/probe must move together: {ad} vs {ps}"
    # widened value (2_000_000 is the known audio-only-lock baseline)
    assert ad == "6000000", (
        f"UDP input probe window must be widened to 6000000 for delayed video; got {ad}"
    )


def _dated_playlist(*entries: tuple[str, str]) -> bytes:
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:3"]
    for name, date in entries:
        lines.extend(("#EXTINF:2.5,", "#EXT-X-PROGRAM-DATE-TIME:" + date, name))
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("probe_delay", [6.118, 2.375])
def test_publisher_dates_follow_measured_media_clock(tmp_path: Path, probe_delay: float) -> None:
    from datetime import UTC, datetime
    from unittest.mock import Mock

    wall = 1793582947.312

    def stamp(value: float) -> str:
        return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds")

    probe = Mock(return_value={"video": 3600.0})
    publisher = _ManifestPublisher(
        manifest_path=tmp_path / "playlist.m3u8",
        staging_path=tmp_path / "playlist.mux.m3u8",
        media_clock=lambda: (17, 3600.0, wall),
        timestamp_probe=probe,
    )
    raw = _dated_playlist(
        ("seg000000001.ts", stamp(wall + probe_delay)),
        ("seg000000002.ts", stamp(wall + probe_delay + 2.5)),
    )
    result = publisher._align_program_dates(raw)
    assert stamp(wall).encode() in result
    assert stamp(wall + 2.5).encode() in result
    assert result.count(b"#EXTINF:2.5,") == 2
    assert probe.call_count == 1
    assert publisher._align_program_dates(raw) == result
    assert probe.call_count == 1


def test_publisher_missing_clock_does_not_manufacture_dates(tmp_path: Path) -> None:
    from unittest.mock import Mock

    probe = Mock(return_value=None)
    publisher = _ManifestPublisher(
        manifest_path=tmp_path / "playlist.m3u8",
        staging_path=tmp_path / "mux.m3u8",
        media_clock=lambda: None,
        timestamp_probe=probe,
    )
    raw = _dated_playlist(("seg000000001.ts", "2026-11-02T01:29:13.430+00:00"))
    result = publisher._align_program_dates(raw)
    assert b"PROGRAM-DATE-TIME" not in result
    assert b"seg000000001.ts" in result
    probe.assert_not_called()


def test_publisher_dates_preserve_prior_window_and_handle_wrap(tmp_path: Path) -> None:
    from datetime import UTC, datetime
    from unittest.mock import Mock

    from civiccast.egress.gst.caption_clock import TS_CLOCK_WRAP_SECONDS

    wall = 1793582947.312
    old = "2026-11-02T01:00:00.000+00:00"
    manifest = tmp_path / "playlist.m3u8"
    manifest.write_bytes(_dated_playlist(("seg000000001.ts", old)))
    probe = Mock(return_value={"video": 0.5})
    publisher = _ManifestPublisher(
        manifest_path=manifest,
        staging_path=tmp_path / "mux.m3u8",
        media_clock=lambda: (17, TS_CLOCK_WRAP_SECONDS - 0.5, wall),
        timestamp_probe=probe,
    )
    raw = _dated_playlist(
        ("seg000000001.ts", old), ("seg000000002.ts", "2026-11-02T01:29:20.000+00:00")
    )
    result = publisher._align_program_dates(raw)
    assert old.encode() in result
    assert (
        datetime.fromtimestamp(wall + 1, UTC).isoformat(timespec="milliseconds").encode() in result
    )
    probe.assert_called_once_with(tmp_path / "seg000000002.ts")


def test_clock_anchor_keeps_first_frame_until_worker_changes() -> None:
    supervisor = HlsRelaySupervisor()
    supervisor.set_media_clock_anchor("public", 17, 3600.0, 1793582947.312)
    supervisor.set_media_clock_anchor("public", 17, 3610.0, 1793582960.0)
    assert supervisor._media_clock_for("public") == (17, 3600.0, 1793582947.312)
    supervisor.set_media_clock_anchor("public", 18, float("nan"), 1793582960.0)
    assert supervisor._media_clock_for("public")[0] == 17
    supervisor.set_media_clock_anchor("public", 18, 3600.0, 1793582960.0)
    assert supervisor._media_clock_for("public") == (18, 3600.0, 1793582960.0)


def test_publisher_repeated_discontinuity_window_does_not_reprobe(tmp_path: Path) -> None:
    from unittest.mock import Mock

    probe = Mock(side_effect=[{"video": 3600.0}, {"video": 3602.5}])
    publisher = _ManifestPublisher(
        manifest_path=tmp_path / "playlist.m3u8",
        staging_path=tmp_path / "mux.m3u8",
        media_clock=lambda: (17, 3600.0, 1793582947.312),
        timestamp_probe=probe,
    )
    raw = _dated_playlist(
        ("seg000000001.ts", "2026-11-02T01:29:13.430+00:00"),
        ("seg000000002.ts", "2026-11-02T01:29:19.430+00:00"),
    ).replace(b"#EXTINF", b"#EXT-X-DISCONTINUITY\n#EXTINF")
    result = publisher._align_program_dates(raw)
    # No publication succeeded yet: a held manifest must retry identically.
    assert publisher._align_program_dates(raw) == result
    assert probe.call_count == 2
    new = _dated_playlist(("seg000000003.ts", "2026-11-02T01:29:21.930+00:00"))
    result = publisher._align_program_dates(raw + new.split(b"#EXT-X-TARGETDURATION:3\n")[1])
    assert b"2026-11-02T01:29:12.312+00:00" in result
    assert probe.call_count == 2


def test_publisher_probe_failure_does_not_retry_unchanged_segment(tmp_path: Path) -> None:
    from unittest.mock import Mock

    probe = Mock(return_value=None)
    publisher = _ManifestPublisher(
        manifest_path=tmp_path / "playlist.m3u8",
        staging_path=tmp_path / "mux.m3u8",
        media_clock=lambda: (17, 3600.0, 1793582947.312),
        timestamp_probe=probe,
    )
    raw = _dated_playlist(("seg000000001.ts", "2026-11-02T01:29:13.430+00:00"))
    assert b"PROGRAM-DATE-TIME" not in publisher._align_program_dates(raw)
    assert b"PROGRAM-DATE-TIME" not in publisher._align_program_dates(raw)
    assert probe.call_count == 1
