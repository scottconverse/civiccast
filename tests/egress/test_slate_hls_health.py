# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Field bug R9 (rc.5, 2026-11-12): a FALLBACK_SLATE channel's web HLS froze.

Channel ``government`` had a failing live source, crash-relaunched ~1904
times and settled on its fallback slate. Its web HLS (the local relay's
segments) froze at 08:10:48 and stayed frozen for over a day while every
health surface said "healthy". Two gaps made that possible:

* the relay checks that decide "this channel is producing" read only the
  daemon's on-air evidence latch (``_on_air_confirmed_at``). That latch is
  cleared on every relaunch, so a slate channel with a live worker was never
  "producing" and its stalled relay was never self-healed (and the U30 freeze
  escalation, which waits on a failed heal, never ran either);
* a slate channel whose relay is not tracked at all (``is_alive`` -> None)
  kept its ``hls`` sink green, because the default sink health treats every
  UDP-like sink as healthy when the channel is not ON_AIR.

Pinned here: a slate channel with a live worker gets the same relay
staleness check and self-heal as an ON_AIR channel, and its ``hls`` sink
reads down while the relay is missing, dead or stalled. STOPPED / DRAINING
channels, channels with no ``hls`` sink, and slate channels whose HLS is
fresh keep their old behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from civiccast.egress.daemon import EgressDaemon
from civiccast.egress.models import (
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressStateRow,
)
from civiccast.egress.source_plan import EgressSourcePlan, EgressSourceSegment
from civiccast.egress.store import InMemoryEgressStore

_CHANNEL = "gov"


class _FakeProcess:
    def __init__(self, *, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self, *, grace_seconds: float = 5.0) -> int | None:
        self.returncode = 0
        return 0


class _FakeRelay:
    """A relay-supervisor double whose verdicts each test sets directly.

    ``alive``: what ``is_alive`` answers (None = no relay tracked).
    ``stale``: what ``progress_stale`` answers.
    """

    def __init__(self, *, alive: bool | None = True, stale: bool | None = False) -> None:
        self.alive = alive
        self.stale = stale
        self.heal_calls: list[dict[str, Any]] = []
        self.restore_calls: list[dict[str, Any]] = []

    def apply(self, config: EgressConfig, **_kwargs: Any) -> EgressConfig:
        return config

    def stop_channel(self, _channel_id: str) -> None:
        return None

    def is_alive(self, _channel_id: str) -> bool | None:
        return self.alive

    def note_progress(self, _channel_id: str, *, now: float) -> None:
        return None

    def progress_stale(self, _channel_id: str, *, now: float) -> bool | None:
        return self.stale

    def never_emitted(self, _channel_id: str, *, now: float, startup_grace_s: float) -> bool | None:
        return None

    def maybe_self_heal_stalled(
        self, channel_id: str, *, now: float, producing: bool, startup_grace_s: float
    ) -> bool:
        self.heal_calls.append({"channel_id": channel_id, "producing": producing})
        return producing

    def maybe_restore_missing_streams(
        self, channel_id: str, *, now: float, producing: bool, startup_grace_s: float
    ) -> bool:
        self.restore_calls.append({"channel_id": channel_id, "producing": producing})
        return False


def _source_plan(tmp_path: Path) -> EgressSourcePlan:
    source = tmp_path / "source-a.ts"
    source.write_text("fake", encoding="utf-8")
    return EgressSourcePlan(
        channel_id=_CHANNEL,
        segments=[
            EgressSourceSegment(
                label="Council meeting",
                path=str(source),
                duration_seconds=1,
                source_ref="asset-council",
            )
        ],
    )


def _daemon(
    tmp_path: Path, relay: _FakeRelay, *, sinks: list[EgressSinkSpec] | None = None
) -> tuple[EgressDaemon, InMemoryEgressStore]:
    """A started channel, using the DEFAULT sink-health builder (no provider)."""

    store = InMemoryEgressStore()
    if sinks is None:
        sinks = [EgressSinkSpec(kind="hls", label="Web", uri=str(tmp_path / "gov-live"))]
    store.upsert_config(
        EgressConfig(channel_id=_CHANNEL, enabled=True, slate_message="slate", sinks=sinks)
    )
    store.enqueue_command(
        EgressCommand(
            channel_id=_CHANNEL,
            action="start",
            issued_at=datetime(2026, 11, 12, 8, 0, tzinfo=UTC),
            issued_by="operator",
            command_id="cmd-start",
        )
    )
    daemon = EgressDaemon(
        store,
        work_dir=tmp_path,
        source_plan_provider=lambda _channel_id: _source_plan(tmp_path),
        ffmpeg_starter=lambda _args: _FakeProcess(),
        hls_relay_supervisor=relay,  # type: ignore[arg-type]
    )
    assert daemon.process_once(_CHANNEL) == 1
    return daemon, store


def _force_state(store: InMemoryEgressStore, state: str) -> None:
    store.write_state(
        EgressStateRow(channel_id=_CHANNEL, state=state, updated_at=datetime.now(UTC))  # type: ignore[arg-type]
    )


def _to_slate_with_no_on_air_latch(daemon: EgressDaemon, store: InMemoryEgressStore) -> None:
    """The field shape: a live worker on the slate, latch cleared by relaunches."""

    _force_state(store, "FALLBACK_SLATE")
    daemon._on_air_confirmed_at.pop(_CHANNEL, None)


def _latest_sinks(store: InMemoryEgressStore) -> dict[str, bool]:
    return store.recent_health(_CHANNEL, 1)[0].sink_connected


# --- (a) stalled relay on slate: down AND healed ------------------------------


def test_slate_channel_with_stalled_relay_reads_down_and_self_heals(tmp_path: Path) -> None:
    relay = _FakeRelay(alive=True, stale=True)
    daemon, store = _daemon(tmp_path, relay)
    _to_slate_with_no_on_air_latch(daemon, store)
    relay.heal_calls.clear()

    daemon.process_once(_CHANNEL)

    assert _CHANNEL not in daemon._on_air_confirmed_at
    assert store.read_state(_CHANNEL).state == "FALLBACK_SLATE"  # type: ignore[union-attr]
    assert _latest_sinks(store) == {"Web": False}
    assert relay.heal_calls, "the stalled relay was never offered a self-heal"
    assert relay.heal_calls[-1]["producing"] is True, (
        "a slate channel with a live worker IS producing (the slate) -- the relay "
        "self-heal must not be gated on the on-air evidence latch alone"
    )


def test_slate_channel_live_relay_stream_restore_treats_the_slate_as_producing(
    tmp_path: Path,
) -> None:
    relay = _FakeRelay(alive=True, stale=False)
    daemon, store = _daemon(tmp_path, relay)
    _to_slate_with_no_on_air_latch(daemon, store)
    relay.restore_calls.clear()

    daemon.process_once(_CHANNEL)

    assert relay.restore_calls[-1]["producing"] is True


# --- (b) slate + live worker + no relay tracked -------------------------------


def test_slate_channel_with_live_worker_and_no_tracked_relay_reads_hls_down(
    tmp_path: Path,
) -> None:
    relay = _FakeRelay(alive=True, stale=False)
    daemon, store = _daemon(tmp_path, relay)
    _to_slate_with_no_on_air_latch(daemon, store)
    relay.alive = None  # the relay is gone from the supervisor's table

    daemon.process_once(_CHANNEL)

    assert store.read_state(_CHANNEL).state == "FALLBACK_SLATE"  # type: ignore[union-attr]
    assert _latest_sinks(store) == {"Web": False}


def test_slate_channel_recovers_green_once_a_relay_is_tracked_and_fresh(tmp_path: Path) -> None:
    relay = _FakeRelay(alive=True, stale=False)
    daemon, store = _daemon(tmp_path, relay)
    _to_slate_with_no_on_air_latch(daemon, store)
    relay.alive = None
    daemon.process_once(_CHANNEL)
    assert _latest_sinks(store) == {"Web": False}

    relay.alive = True
    daemon.process_once(_CHANNEL)

    assert _latest_sinks(store) == {"Web": True}


# --- (c) slate + fresh relay stays healthy ------------------------------------


def test_slate_channel_with_fresh_relay_stays_healthy(tmp_path: Path) -> None:
    relay = _FakeRelay(alive=True, stale=False)
    daemon, store = _daemon(tmp_path, relay)
    _to_slate_with_no_on_air_latch(daemon, store)
    relay.heal_calls.clear()

    daemon.process_once(_CHANNEL)

    assert _latest_sinks(store) == {"Web": True}
    assert relay.heal_calls == []


def test_slate_channel_relay_window_not_yet_readable_is_not_flagged(tmp_path: Path) -> None:
    """``progress_stale`` None (no window yet, inside grace) stays unknown."""

    relay = _FakeRelay(alive=True, stale=None)
    daemon, store = _daemon(tmp_path, relay)
    _to_slate_with_no_on_air_latch(daemon, store)

    daemon.process_once(_CHANNEL)

    assert _latest_sinks(store) == {"Web": True}


# --- (d) unchanged: STOPPED / DRAINING / no hls sink / ON_AIR without latch ----


def test_stopped_channel_without_a_relay_is_unchanged(tmp_path: Path) -> None:
    relay = _FakeRelay(alive=None, stale=None)
    daemon, store = _daemon(tmp_path, relay)
    daemon._processes.pop(_CHANNEL, None)
    _force_state(store, "STOPPED")
    relay.heal_calls.clear()

    daemon._poll_hls_relay(_CHANNEL)

    assert _CHANNEL not in daemon._hls_relay_dead
    assert relay.heal_calls == []


def test_stopped_channel_stalled_relay_is_not_healed(tmp_path: Path) -> None:
    relay = _FakeRelay(alive=True, stale=True)
    daemon, store = _daemon(tmp_path, relay)
    daemon._processes.pop(_CHANNEL, None)
    daemon._on_air_confirmed_at.pop(_CHANNEL, None)
    _force_state(store, "STOPPED")
    relay.heal_calls.clear()

    daemon._poll_hls_relay(_CHANNEL)

    assert all(call["producing"] is False for call in relay.heal_calls)


def test_draining_slate_channel_without_a_relay_is_unchanged(tmp_path: Path) -> None:
    relay = _FakeRelay(alive=True, stale=False)
    daemon, store = _daemon(tmp_path, relay)
    _to_slate_with_no_on_air_latch(daemon, store)
    daemon._draining_channels.add(_CHANNEL)
    relay.alive = None

    daemon._poll_hls_relay(_CHANNEL)

    assert _CHANNEL not in daemon._hls_relay_dead


def test_slate_channel_without_an_hls_sink_is_unchanged(tmp_path: Path) -> None:
    relay = _FakeRelay(alive=None, stale=None)
    daemon, store = _daemon(
        tmp_path,
        relay,
        sinks=[EgressSinkSpec(kind="udp-ts", label="Cable", uri="udp://239.1.1.1:5000")],
    )
    _to_slate_with_no_on_air_latch(daemon, store)

    daemon.process_once(_CHANNEL)

    assert _CHANNEL not in daemon._hls_relay_dead
    assert _latest_sinks(store) == {"Cable": True}


def test_on_air_channel_without_latch_still_does_not_self_heal(tmp_path: Path) -> None:
    """STARTING-like ON_AIR (no on-air evidence yet) keeps the old gate."""

    relay = _FakeRelay(alive=True, stale=True)
    daemon, store = _daemon(tmp_path, relay)
    daemon._on_air_confirmed_at.pop(_CHANNEL, None)
    relay.heal_calls.clear()

    daemon.process_once(_CHANNEL)

    assert store.read_state(_CHANNEL).state == "ON_AIR"  # type: ignore[union-attr]
    assert all(call["producing"] is False for call in relay.heal_calls)
