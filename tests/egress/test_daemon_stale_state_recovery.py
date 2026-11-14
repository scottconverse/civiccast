# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Restart-recovery: a persisted ON_AIR row whose encoder PID is dead must be
reconciled on fresh daemon startup, not trusted forever.

Live defect (2026-09-24): after a guarded four-file stage restarted
CivicCastSupervisor, all three live-HLS playlists froze (VTT 7B) while
``egress_states`` still read ON_AIR with PIDs that no longer existed; the
command ledger was empty (no manual controls). Recovery is driven by
``ChannelAutomationService`` re-issuing ``start`` for channels with no live
encoder -- but a persisted-ON_AIR row whose PID is dead has no *tracked*
process on a fresh daemon, so it must be reconciled rather than trusted.

These tests pin the startup reconciliation contract:
* persisted ON_AIR + dead PID -> reported recoverable and the stale claim cleared;
* persisted ON_AIR + LIVE PID -> untouched, no duplicate start;
* explicit STOPPED -> untouched;
* reconciliation is idempotent (bounded, no restart loop).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from civiccast.egress.automation import ChannelAutomationService, ChannelAutomationSettings
from civiccast.egress.daemon import EgressDaemon, OrphanInfo
from civiccast.egress.models import (
    EgressCommand,
    EgressConfig,
    EgressSinkSpec,
    EgressState,
    EgressStateRow,
    restart_recovery_previous_state,
)
from civiccast.egress.store import InMemoryEgressStore

_ON_AIR_STATES = {"ON_AIR", "STARTING", "TRANSITIONING", "FALLBACK_SLATE", "DRAINING"}


def _config(*, auto_start: bool) -> EgressConfig:
    return EgressConfig(
        channel_id="gov",
        enabled=True,
        auto_start=auto_start,
        slate_message="Off air",
        sinks=[EgressSinkSpec(kind="file", label="Proof", uri="build/out.ts")],
    )


def _on_air(pid: int | None) -> EgressStateRow:
    return EgressStateRow(
        channel_id="gov",
        state="ON_AIR",
        updated_at=datetime.now(UTC) - timedelta(minutes=60),
        pid=pid,
    )


def _row(state: EgressState, *, pid: int | None, age_seconds: float = 3600.0) -> EgressStateRow:
    """A persisted row written ``age_seconds`` ago (``updated_at`` is the row's
    own last write -- see ``_write_state``)."""
    return EgressStateRow(
        channel_id="gov",
        state=state,
        updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        pid=pid,
    )


def _table_seams(
    table: dict[int, OrphanInfo],
) -> tuple[Callable[[int], bool | None], Callable[[int], OrphanInfo | None]]:
    """``(pid_is_dead, orphan_probe)`` over ONE fake host process table.

    This table is the whole process world for the tests that use it: a pid in
    it exists and its identity (image name + create time) is readable; a pid
    absent from it is definitively gone. Nothing here touches a real process.
    """

    return (lambda pid: pid not in table, lambda pid: table.get(pid))


def _automation(store: InMemoryEgressStore, daemon: EgressDaemon) -> ChannelAutomationService:
    """A ChannelAutomationService over this store+daemon (test seam)."""
    return ChannelAutomationService(
        store,
        daemon,
        lambda _ch: None,
        settings=ChannelAutomationSettings(),
    )


class _RecordingDaemon(EgressDaemon):
    """Daemon that records start intents instead of spawning encoders.

    Offline test boundary: ``_start`` never launches a real encoder, so the
    recovery decision is asserted without any process/CPU work.
    """

    def __init__(
        self,
        store: InMemoryEgressStore,
        *,
        work_dir: Path,
        live_pids: set[int] | None = None,
        pid_is_dead: object | None = None,
        orphan_probe: Callable[[int], OrphanInfo | None] | None = None,
    ) -> None:
        super().__init__(
            store,
            work_dir=work_dir,
            source_plan_provider=lambda _ch: None,
            # Deterministic tri-state liveness for tests: a pid in live_pids is
            # alive (False), anything else is definitively dead (True); tests
            # that need UNKNOWN inject ``pid_is_dead=lambda _p: None``.
            pid_is_dead=pid_is_dead
            if pid_is_dead is not None
            else (lambda p: p not in (live_pids or set())),
            # U06: the identity probe is a separate seam (create-time), so a
            # test can model pid REUSE -- a pid that exists but belongs to a
            # process other than the encoder the row describes.
            orphan_probe=orphan_probe,
        )
        self._live_pids = live_pids or set()
        self.starts: list[str] = []
        self.start_kwargs: list[dict[str, object]] = []

    def has_live_process(self, channel_id: str) -> bool:
        # Fresh daemon: nothing is tracked in memory. A "live" channel in these
        # tests is one whose persisted pid the host still reports alive.
        row = self._store.read_state(channel_id)
        return row is not None and row.pid is not None and row.pid in self._live_pids

    def _start(self, channel_id: str, **kwargs: object) -> None:
        self.starts.append(channel_id)
        self.start_kwargs.append(kwargs)


def test_persisted_on_air_with_dead_pid_is_reconciled_on_fresh_start(tmp_path: Path) -> None:
    """RED: stale ON_AIR + dead PID must be recoverable, not trusted forever."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=999999))

    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    recovered = daemon.reconcile_stale_state()

    assert recovered == ["gov"], (
        "a persisted on-air row whose PID is dead must be reported recoverable "
        f"on fresh startup; got {recovered!r}"
    )
    row = store.read_state("gov")
    assert row is not None and row.state not in _ON_AIR_STATES, (
        f"stale on-air claim was left standing: {row.state if row else None}"
    )


def test_live_pid_is_not_reconciled_or_restarted(tmp_path: Path) -> None:
    """A persisted ON_AIR whose PID is genuinely alive must be left alone.

    This exercises the liveness + identity guard directly (not the in-memory
    ``has_live_process`` short-circuit): the daemon tracks no process for the
    channel, so reconciliation reaches both probes, which report the pid still
    alive AND held by a process that started before the row was written (the
    identity of a real encoder, see ``test_stale_row_whose_pid_now_belongs_to_
    an_unrelated_process_recovers`` for the reused-pid case) -> the row must be
    untouched.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=4242))
    # The definitive tri-state check reports the pid ALIVE (False), and the
    # identity probe reports it as ours (created before the row's own write).
    daemon = _RecordingDaemon(
        store,
        work_dir=tmp_path,
        pid_is_dead=lambda _p: False,
        orphan_probe=lambda _p: OrphanInfo(name="ffmpeg.exe", created_at=time.time() - 7200.0),
    )

    recovered = daemon.reconcile_stale_state()

    assert recovered == [], f"a genuinely live PID must not be reconciled; got {recovered!r}"
    assert daemon.starts == [], "no start may be issued for a genuinely live channel"
    row = store.read_state("gov")
    assert row is not None and row.state == "ON_AIR"


def test_dead_pid_probe_clears_claim(tmp_path: Path) -> None:
    """Direct probe-based check: probe None (no such process) => reconcile."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=777777))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)
    daemon._orphan_probe = lambda pid: None  # dead/reaped: no such process

    assert daemon.reconcile_stale_state() == ["gov"]


def test_explicit_stopped_is_never_reconciled(tmp_path: Path) -> None:
    """STOPPED is explicit operator intent: reconciliation must not touch it."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="STOPPED",
            updated_at=datetime.now(UTC) - timedelta(minutes=60),
            pid=None,
        )
    )
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    assert daemon.reconcile_stale_state() == []
    row = store.read_state("gov")
    assert row is not None and row.state == "STOPPED"


def test_reconciliation_is_idempotent(tmp_path: Path) -> None:
    """A second pass must not re-report or loop on the same channel."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=999999))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    first = daemon.reconcile_stale_state()
    second = daemon.reconcile_stale_state()

    assert first == ["gov"]
    assert second == [], (
        "reconciliation must be idempotent -- a second pass must not re-report "
        f"the same channel; got {second!r}"
    )


# --- repeat-restart + atomicity (coordinator audit) --------------------------
#
# Two real hazards the first cut missed:
#  1. A CONSTANT command_id ("restart-recovery-start-<channel>") is skipped
#     forever by the SQL store (store.enqueue_commands skips an existing id),
#     so a SECOND stale recovery would write STOPPED then queue nothing ->
#     permanently dark.
#  2. _write_state(STOPPED) and enqueue_command were TWO commits; a crash
#     between them leaves STOPPED with no queued start, and STOPPED is not
#     reconciled, so the next startup never retries.
#
# The fix: one atomic store operation that enqueues the start AND clears the
# stale claim in a single commit, with a UNIQUE, bounded command id per
# recovery attempt (so a later restart queues a fresh start, while an immediate
# replay of the same attempt is idempotent).


def test_repeat_restart_queues_a_fresh_start_each_time(tmp_path: Path) -> None:
    """RED: two successive stale recoveries must both queue a start.

    A constant command_id would be skipped the second time (SQL store skips an
    existing id), leaving the channel STOPPED and dark.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    # First restart: stale ON_AIR + dead pid.
    store.write_state(_on_air(pid=111111))
    assert daemon.reconcile_stale_state() == ["gov"]

    # Second restart later: the channel went stale again with a new dead pid.
    store.write_state(_on_air(pid=222222))
    assert daemon.reconcile_stale_state() == ["gov"], (
        "a SECOND restart-recovery must also queue a start; a constant "
        "command_id makes the SQL store skip it and the channel stays dark"
    )

    starts = store.peek_pending_commands("gov")
    assert len(starts) == 2, f"expected two queued starts, got {len(starts)}"
    ids = {cmd.command_id for cmd in starts}
    assert len(ids) == 2, f"recovery starts must have distinct ids, got {ids}"
    assert all(len(cid) <= 120 for cid in ids), f"command id must be <=120 chars: {ids}"


def test_recovery_is_atomic_state_and_command_together(tmp_path: Path) -> None:
    """RED: clearing the stale claim and queueing the start must not be
    separable -- no crash window where STOPPED exists with no start queued."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=333333))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    daemon.reconcile_stale_state()

    row = store.read_state("gov")
    starts = store.peek_pending_commands("gov")
    # Either BOTH happened (recovered) or the state is still ON_AIR (not yet
    # recovered) -- never STOPPED with an empty queue.
    if row is not None and row.state not in _ON_AIR_STATES:
        assert starts, (
            "state was cleared to non-on-air but no start was queued: a crash "
            "between the two commits would strand the channel dark"
        )


def test_reconcile_idempotent_within_one_attempt(tmp_path: Path) -> None:
    """Re-running reconciliation without a NEW stale write must not re-queue."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=444444))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    assert daemon.reconcile_stale_state() == ["gov"]
    assert daemon.reconcile_stale_state() == []
    assert len(store.peek_pending_commands("gov")) == 1


def test_unknown_liveness_fails_closed(tmp_path: Path) -> None:
    """AccessDenied/unknown MUST NOT be reconciled (fail closed, never dark)."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=555555))
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: None)

    assert daemon.reconcile_stale_state() == [], (
        "unknown liveness (e.g. AccessDenied) must fail CLOSED -- never clear "
        "the claim, because the pid might still be alive"
    )
    row = store.read_state("gov")
    assert row is not None and row.state == "ON_AIR"
    assert store.peek_pending_commands("gov") == []


def test_alive_pid_not_reconciled_via_tristate(tmp_path: Path) -> None:
    """A liveness=False (alive) result must be left untouched."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=666666))
    daemon = _RecordingDaemon(
        store,
        work_dir=tmp_path,
        pid_is_dead=lambda _p: False,
        orphan_probe=lambda _p: OrphanInfo(name="ffmpeg.exe", created_at=time.time() - 7200.0),
    )

    assert daemon.reconcile_stale_state() == []
    row = store.read_state("gov")
    assert row is not None and row.state == "ON_AIR"


def test_definitively_dead_pid_uses_atomic_store_op(tmp_path: Path) -> None:
    """The recovery must go through the single atomic store operation."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=777777))
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)

    calls = {"n": 0}
    real = store.recover_stale_state

    def _spy(row, command):
        calls["n"] += 1
        return real(row, command)

    store.recover_stale_state = _spy  # type: ignore[method-assign]
    assert daemon.reconcile_stale_state() == ["gov"]
    assert calls["n"] == 1, "recovery must use the atomic store op exactly once"


# --- SQL fail-closed IntegrityError + InMemory thread-safety (audit rev2) ----


def _fake_pg_store(fail_commit: bool):
    """A PostgresEgressStore over a fake session factory.

    ``fail_commit=True`` makes the single combined commit raise IntegrityError,
    so the test can assert NOTHING was committed (no state-only retry).
    Records any state write that actually commits in ``committed_states``.
    """
    from sqlalchemy.exc import IntegrityError

    from civiccast.egress.store import PostgresEgressStore

    committed_states: list[str] = []

    class _Res:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _FakeSession:
        def __init__(self) -> None:
            self._has_state = False

        def execute(self, stmt):
            return _Res(None)

        def add(self, obj):
            if type(obj).__name__ == "EgressStateDb":
                self._has_state = True

        def commit(self):
            if fail_commit:
                raise IntegrityError("stmt", {}, Exception("integrity failure"))
            if self._has_state:
                committed_states.append("state-committed")

        def rollback(self):
            self._has_state = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Factory:
        def __call__(self):
            return _FakeSession()

    store = PostgresEgressStore(_Factory())  # type: ignore[arg-type]
    return store, committed_states


def test_sql_recover_raises_and_writes_nothing_on_integrity_error() -> None:
    """ANY IntegrityError => fail closed: roll back, re-raise, NO state write.

    Covers a consumed id, a wrong-channel/action collision, and an unrelated
    constraint failure alike: none may leave STOPPED without a pending start.
    """
    from sqlalchemy.exc import IntegrityError

    store, committed_states = _fake_pg_store(fail_commit=True)
    row = EgressStateRow(channel_id="gov", state="STOPPED", updated_at=datetime.now(UTC), pid=None)
    cmd = EgressCommand(
        channel_id="gov",
        action="start",
        issued_at=datetime.now(UTC),
        issued_by="restart-recovery",
        command_id="restart-recovery-dup",
    )

    with pytest.raises(IntegrityError):
        store.recover_stale_state(row, cmd)

    assert committed_states == [], (
        f"no state-only commit may occur on any IntegrityError; committed {committed_states}"
    )


def test_inmemory_duplicate_command_id_fails_closed(tmp_path: Path) -> None:
    """A duplicate/consumed command id must NOT publish a STOPPED state.

    The caller uses fresh uuid4 ids, so a duplicate means something is wrong;
    the safe action is to change nothing and raise.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=101010))
    cmd = EgressCommand(
        channel_id="gov",
        action="start",
        issued_at=datetime.now(UTC),
        issued_by="restart-recovery",
        command_id="restart-recovery-fixed-id",
    )
    # Simulate the id already existing (pending) from a prior attempt.
    store.enqueue_command(cmd)

    row = EgressStateRow(channel_id="gov", state="STOPPED", updated_at=datetime.now(UTC), pid=None)
    with pytest.raises(ValueError, match="already exists"):
        store.recover_stale_state(row, cmd)

    # The stale ON_AIR row must survive untouched (no STOPPED published).
    assert store.read_state("gov") is not None
    assert store.read_state("gov").state == "ON_AIR"


def test_inmemory_wrong_channel_duplicate_fails_closed(tmp_path: Path) -> None:
    """A same-id command for a DIFFERENT channel must also fail closed."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=202020))
    other = EgressCommand(
        channel_id="other",
        action="start",
        issued_at=datetime.now(UTC),
        issued_by="restart-recovery",
        command_id="restart-recovery-collide",
    )
    store.enqueue_command(other)

    row = EgressStateRow(channel_id="gov", state="STOPPED", updated_at=datetime.now(UTC), pid=None)
    colliding = EgressCommand(
        channel_id="gov",
        action="start",
        issued_at=datetime.now(UTC),
        issued_by="restart-recovery",
        command_id="restart-recovery-collide",
    )
    with pytest.raises(ValueError, match="already exists"):
        store.recover_stale_state(row, colliding)
    assert store.read_state("gov").state == "ON_AIR"


def test_inmemory_recover_publishes_command_before_state(tmp_path: Path) -> None:
    """A reader must never see STOPPED without the start command present.

    Spawn a reader thread hammering the store while recovery runs; every
    observation of the STOPPED row must coincide with the queued command.
    """
    import threading

    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_on_air(pid=888888))

    stop = threading.Event()
    violations: list[tuple] = []

    def _reader() -> None:
        while not stop.is_set():
            row = store.read_state("gov")
            if (
                row is not None
                and row.state == "STOPPED"
                and not store.peek_pending_commands("gov")
            ):
                violations.append((row.state, tuple(store.peek_pending_commands("gov"))))

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    try:
        daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)
        for _ in range(50):
            store.write_state(_on_air(pid=888888))
            daemon.reconcile_stale_state()
    finally:
        stop.set()
        reader.join(timeout=5)

    assert violations == [], f"observed STOPPED with no queued start {len(violations)}x"


# --- coordinator rev5: DRAINING terminal intent + auto_start duplicate -------


def test_persisted_draining_with_dead_pid_is_never_restarted(tmp_path: Path) -> None:
    """RED: DRAINING is explicit off-air intent -- restart must NOT auto-start it.

    _drain sets DRAINING for operator/supervisor off-air intent; a restart that
    found a dead pid mid-drain must leave it off air (STOPPED), never queue a
    start.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(
        EgressStateRow(
            channel_id="gov",
            state="DRAINING",
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
            pid=909090,
        )
    )
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)

    recovered = daemon.reconcile_stale_state()

    assert recovered == [], (
        "DRAINING is terminal off-air intent; restart recovery must not "
        f"auto-start it; got {recovered!r}"
    )
    assert store.peek_pending_commands("gov") == [], "no start may be queued for a drained channel"


def test_auto_start_does_not_double_enqueue_after_recovery(tmp_path: Path) -> None:
    """RED: a stale ON_AIR auto_start channel must get ONE start, not two.

    run_forever reconciles (queues a recovery start), then the first
    _run_channel_pass sees no live process + auto_start and would queue a
    SECOND start. One operator intent => one start.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(_on_air(pid=808080))
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)

    # Startup reconciliation queues the recovery start (and clears the stale row
    # to STOPPED).
    assert daemon.reconcile_stale_state() == ["gov"]

    # Now simulate the first automation pass: no live process + auto_start.
    # _run_channel_pass may enqueue an auto_start AND then process_once drains
    # the queue, so count every start the daemon actually PROCESSED (recorded
    # by _RecordingDaemon._start) plus anything left pending.
    automation = _automation(store, daemon)
    automation._run_channel_pass(store.get_config("gov"), "gov", datetime.now(UTC))

    processed = len(daemon.starts)
    pending = [c for c in store.peek_pending_commands("gov") if c.action == "start"]
    total = processed + len(pending)
    assert total == 1, (
        "a stale ON_AIR auto_start channel must receive exactly ONE start; "
        f"processed={processed} pending={len(pending)} "
        f"pending_by={[c.issued_by for c in pending]}"
    )


# --- REV5 precision: distinguish MY fix from pre-existing auto_start policy ---


def test_reconcile_does_not_recover_draining_even_when_auto_start(tmp_path: Path) -> None:
    """MY fix's own guarantee: reconciliation alone never recovers DRAINING.

    This is deliberately narrower than "the channel stays off air": the
    separate, PRE-EXISTING auto_start policy (`_run_channel_pass` enqueues a
    start for any enabled auto_start channel with no live process, without
    consulting the persisted state) can still bring it back. That policy is
    captured by the companion test below, NOT by reconciliation.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(
        EgressStateRow(channel_id="gov", state="DRAINING", updated_at=datetime.now(UTC), pid=909090)
    )
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)

    assert daemon.reconcile_stale_state() == []
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "DRAINING", (
        "reconciliation must not rewrite the DRAINING row"
    )


def test_preexisting_auto_start_still_starts_draining_channel(tmp_path: Path) -> None:
    """Documents PRE-EXISTING policy: auto_start starts a DRAINING channel.

    `_run_channel_pass` gates only on has_live_process + config.auto_start; it
    never consults persisted state. So an auto_start channel drained just before
    a restart IS brought back on air -- by that policy, independent of restart
    reconciliation. Pinned here so the behavior is explicit and cannot be
    mistaken for a reconciliation guarantee.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(
        EgressStateRow(channel_id="gov", state="DRAINING", updated_at=datetime.now(UTC), pid=909090)
    )
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)
    daemon.reconcile_stale_state()  # no-op for DRAINING

    automation = _automation(store, daemon)
    automation._run_channel_pass(store.get_config("gov"), "gov", datetime.now(UTC))

    processed = len(daemon.starts)
    pending = [c for c in store.peek_pending_commands("gov") if c.action == "start"]
    assert processed + len(pending) == 1, (
        "pre-existing auto_start policy starts a DRAINING auto_start channel; "
        f"processed={processed} pending={len(pending)}"
    )


def test_draining_non_auto_start_stays_off_air_after_restart(tmp_path: Path) -> None:
    """The safe, common case: DRAINING + auto_start=False => nothing starts it."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(
        EgressStateRow(channel_id="gov", state="DRAINING", updated_at=datetime.now(UTC), pid=909090)
    )
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)
    assert daemon.reconcile_stale_state() == []

    automation = _automation(store, daemon)
    automation._run_channel_pass(store.get_config("gov"), "gov", datetime.now(UTC))

    assert daemon.starts == [], "a non-auto_start DRAINING channel must not start"
    assert store.peek_pending_commands("gov") == []


def test_auto_start_retry_still_fires_when_nothing_pending(tmp_path: Path) -> None:
    """No retry regression: with no pending start, auto_start still enqueues and
    the retry cooldown re-arms so a dark auto_start channel is retried."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    # Dark, no persisted state at all (fresh boot before any start).
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)
    auto = _automation(store, daemon)

    auto._run_channel_pass(store.get_config("gov"), "gov", datetime.now(UTC))
    assert "gov" in auto._start_retry_at, "retry cooldown must be armed"
    first = len(daemon.starts) + len(
        [c for c in store.peek_pending_commands("gov") if c.action == "start"]
    )
    assert first == 1, f"auto_start must still enqueue when nothing pending; got {first}"


def test_auto_start_retry_cooldown_reissues_after_window(tmp_path: Path) -> None:
    """After the cooldown elapses with nothing pending, auto_start re-issues."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)
    # Simulate a large future monotonic so the retry window has elapsed.
    daemon._monotonic = lambda: 10_000.0
    auto = _automation(store, daemon)
    auto._monotonic = lambda: 10_000.0
    auto._start_retry_at["gov"] = 1.0  # armed long ago
    auto._run_channel_pass(store.get_config("gov"), "gov", datetime.now(UTC))
    assert "gov" in auto._start_retry_at, "retry cooldown re-armed after reissue"
    total = len(daemon.starts) + len(
        [c for c in store.peek_pending_commands("gov") if c.action == "start"]
    )
    assert total == 1, f"auto_start must reissue after the cooldown; got {total}"


def test_pending_start_guard_would_also_apply_to_sql_pending_start(tmp_path: Path) -> None:
    """Store parity: the guard reads ``peek_pending_commands``, the SAME contract
    both InMemory and Postgres implement. A pending start returned by the store
    (as Postgres returns consumed_at IS NULL rows) suppresses the auto_start
    duplicate; here we assert it through the store seam used by production."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    daemon = _RecordingDaemon(store, work_dir=tmp_path, pid_is_dead=lambda _p: True)
    auto = _automation(store, daemon)
    # A recovery-style start is already pending (what reconcile queues).
    store.enqueue_command(
        EgressCommand(
            channel_id="gov",
            action="start",
            issued_at=datetime.now(UTC),
            issued_by="restart-recovery",
            command_id="restart-recovery-parity",
        )
    )
    auto._run_channel_pass(store.get_config("gov"), "gov", datetime.now(UTC))
    total = len(daemon.starts) + len(
        [c for c in store.peek_pending_commands("gov") if c.action == "start"]
    )
    assert total == 1, (
        "the guard must not add a second start on top of an already-pending "
        f"start from the store; got {total}"
    )


# --- U06: pid REUSE after a reboot, pid-less rows, later ticks ---------------
#
# Gap 2 (pid reuse). ``_pid_is_dead`` answers only "does SOME process hold this
# pid?". After a reboot Windows hands a dead encoder's pid to an unrelated
# process, so the stale row's pid reads as ALIVE and the channel is never
# recovered -- the exact 2026-09-24 shape. The question reconciliation has to
# answer is "is this OUR encoder?", and the identity evidence available is
# process AGE: the row describes a process that was already running when the
# row itself was last written (``_write_state`` records ``updated_at`` while
# polling a LIVE process -- see ``_poll_process``), so a process created AFTER
# that instant is not the one the row describes.
#
# Gap 1 (pid-less rows). A STARTING/ON_AIR row with NO pid is a crash before the
# pid was ever recorded. On the FIRST tick after a boot, a fresh daemon provably
# owns no encoder for any channel, so that claim belongs to a predecessor. On a
# LATER tick the same shape can be this daemon's OWN in-flight work -- the
# deferred crash-relaunch branch writes STARTING with no pid for the whole
# back-off cooldown -- so it must be left alone there.
#
# Gap 3 (later ticks). The one-shot startup sweep cannot see a row whose pid was
# still alive at that instant and died afterwards: ``_poll_process`` returns
# immediately for a channel with no tracked process, and a non-auto_start
# channel has no other supervisor.


def test_stale_row_whose_pid_now_belongs_to_an_unrelated_process_recovers(
    tmp_path: Path,
) -> None:
    """RED (U06 gap 2): pid exists -- but it is not our encoder.

    The row was written an hour before the crash; the process holding the pid
    now started after that, so it cannot be the encoder the row describes.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("ON_AIR", pid=4321, age_seconds=3600))
    pid_is_dead, orphan_probe = _table_seams(
        {4321: OrphanInfo(name="notepad.exe", created_at=time.time())}
    )
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )

    assert daemon.reconcile_stale_state() == ["gov"], (
        "a pid REUSED by an unrelated process after a reboot must not be read "
        "as a live encoder; the channel must be recovered"
    )
    assert [c.action for c in store.peek_pending_commands("gov")] == ["start"]


def test_pid_held_by_a_process_older_than_the_row_is_left_alone(tmp_path: Path) -> None:
    """The counterpart: an encoder that was already running when the row was
    written is OURS, and is never touched.

    The image name is ``python.exe`` on purpose: the encoder worker runs as
    ``<interpreter> <worker-script> <graph> <control-channel>``
    (``gst/strategy.py``), so the name is shared with the control plane and
    with every conform job -- age is the discriminating evidence, not the name.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("ON_AIR", pid=4321, age_seconds=300))
    pid_is_dead, orphan_probe = _table_seams(
        {4321: OrphanInfo(name="python.exe", created_at=time.time() - 3600.0)}
    )
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )

    assert daemon.reconcile_stale_state() == []
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "ON_AIR"


def test_unreadable_pid_identity_fails_closed(tmp_path: Path) -> None:
    """The pid exists but its identity cannot be read (AccessDenied, or it
    exited between the two probes): fail CLOSED -- never clear the claim."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("ON_AIR", pid=4321, age_seconds=300))
    daemon = _RecordingDaemon(
        store,
        work_dir=tmp_path,
        pid_is_dead=lambda _p: False,  # the pid exists ...
        orphan_probe=lambda _p: None,  # ... but who holds it is unknown
    )

    assert daemon.reconcile_stale_state() == []
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "ON_AIR"


def test_pidless_starting_row_is_recovered_on_the_first_tick(tmp_path: Path) -> None:
    """RED (U06 gap 1): a crash mid-start leaves STARTING with no pid at all.

    Nothing else recovers it: ``has_live_process`` is False on a fresh daemon,
    so a non-auto_start channel has no supervisor left, and STOPPED (what a
    completed stop writes) is not what is persisted here.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("STARTING", pid=None))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    assert daemon.reconcile_stale_state() == ["gov"]
    assert [c.action for c in store.peek_pending_commands("gov")] == ["start"]

    # ... exactly once: the row is STOPPED now, so a re-run reports nothing.
    assert daemon.reconcile_stale_state() == []
    assert len(store.peek_pending_commands("gov")) == 1


def test_pidless_on_air_row_is_recovered_on_the_first_tick(tmp_path: Path) -> None:
    """``_start_steps`` publishes ON_AIR before the worker exists (pid=None), so
    a predecessor that died in that window leaves this shape behind."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("ON_AIR", pid=None))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    assert daemon.reconcile_stale_state() == ["gov"]


class _ExitedWorker:
    """A worker this daemon still tracks whose process has ALREADY exited (it
    died inside the gap between two automation passes)."""

    pid = 4321

    def poll(self) -> int | None:
        return 0

    def terminate(self) -> None:  # pragma: no cover - never called here
        raise AssertionError("no encoder may be terminated by reconciliation")


def test_tracked_worker_that_just_exited_is_left_to_poll_process(tmp_path: Path) -> None:
    """RED (U06): a worker this daemon TRACKS is never reconciled, even when it
    has already exited.

    ``_poll_process`` owns that transition -- the crash-relaunch path, its
    back-off latch and its streak/fallback escalation all key off the persisted
    row still reading ON_AIR. Clearing the row here would replace that pacing
    with a bare start on the same tick.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(_row("ON_AIR", pid=4321, age_seconds=5))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)
    daemon._processes["gov"] = _ExitedWorker()

    assert daemon.reconcile_stale_state() == [], (
        "a tracked (even just-exited) worker belongs to _poll_process; "
        "reconciliation must not clear its row or queue a start"
    )
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "ON_AIR"


def test_operator_stopped_channel_is_never_recovered(tmp_path: Path) -> None:
    """STOPPED is explicit off-air intent, on the first tick and every later one.

    Reconciliation never brings it back. NOTE the separate, PRE-EXISTING
    auto_start policy (`_run_channel_pass_body`) does start any enabled
    auto_start channel with no live process without consulting the stored state
    -- pinned by ``test_preexisting_auto_start_still_starts_draining_channel``
    and raised with the coordinator rather than changed here (U06 gap 4).
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(_row("STOPPED", pid=None))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    assert daemon.reconcile_stale_state() == []
    assert daemon.reconcile_stale_state() == []
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "STOPPED"


def test_preexisting_auto_start_still_starts_a_stopped_channel(tmp_path: Path) -> None:
    """OBSERVED (U06 gap 4): on a later pass, an auto_start channel that was
    STOPPED is started again -- by the PRE-EXISTING auto_start policy, not by
    reconciliation.

    ``_run_channel_pass_body`` gates only on ``has_live_process`` +
    ``config.auto_start`` + the retry cooldown + the pending-start guard; it
    never reads ``egress_states``. So a STOPPED row does not keep an auto_start
    channel off air across a restart, whether that STOPPED came from the
    operator's own ``stop`` command (daemon.py:1345), from the worker's clean
    exit / a completed drain (daemon.py:2640), or from a drain with nothing to
    drain (daemon.py:4092) -- all three write the same row shape. Raised with
    the coordinator as a product question (``questions/U06.md``) rather than
    changed here; this test is the evidence for it.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(_row("STOPPED", pid=None, age_seconds=5))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)
    service = _automation(store, daemon)

    assert daemon.reconcile_stale_state() == [], "reconciliation never recovers STOPPED"

    service.run_once(now=datetime.now(UTC))

    assert daemon.starts == ["gov"], (
        "the pre-existing auto_start policy starts a STOPPED auto_start channel "
        f"on the next pass; starts={daemon.starts}"
    )
    assert daemon.starts.count("gov") == 1, "exactly once, no duplicate"


def test_operator_stopped_channel_with_a_dead_pid_is_never_recovered(tmp_path: Path) -> None:
    """Same for a STOPPED row that still carries the pid of the stopped worker."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(_row("STOPPED", pid=4321))
    pid_is_dead, orphan_probe = _table_seams({})  # the pid is gone
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )

    assert daemon.reconcile_stale_state() == []
    assert store.peek_pending_commands("gov") == []


def test_encoder_alive_at_startup_that_dies_later_is_recovered_on_a_later_tick(
    tmp_path: Path,
) -> None:
    """RED (U06 gap 3): alive at the startup sweep, gone by the next pass.

    Driven through ``run_once`` -- a real automation pass -- because that is
    what has to notice it. The channel is NOT ``auto_start``, so nothing else
    in the pass may start it: the only thing that can recover it is the sweep
    itself.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("ON_AIR", pid=4321, age_seconds=60))
    table = {4321: OrphanInfo(name="python.exe", created_at=time.time() - 3600.0)}
    pid_is_dead, orphan_probe = _table_seams(table)
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )
    service = _automation(store, daemon)
    now = datetime.now(UTC)

    # Startup sweep: the encoder is still alive -> untouched, nothing queued.
    assert daemon.reconcile_stale_state() == []
    assert store.peek_pending_commands("gov") == []

    # It is still alive for the next pass, which must also leave it alone.
    service.run_once(now=now)
    assert daemon.starts == [], "a live encoder must never be restarted by a pass"

    # It dies while the service keeps running.
    table.clear()

    service.run_once(now=now)
    assert daemon.starts == ["gov"], (
        "a row whose encoder died AFTER the startup sweep must still be "
        "reconciled -- otherwise a non-auto_start channel stays dark forever"
    )
    # The recovery start is queued BEFORE the per-channel loop and drained by
    # that channel's own ``process_once`` in the same pass, so it is processed
    # rather than left pending -- and exactly once.
    assert store.peek_pending_commands("gov") == [], "the recovery start was drained"
    service.run_once(now=now)
    assert daemon.starts == ["gov"], f"recovery is once: {daemon.starts}"


def test_per_pass_recovery_does_not_double_start_an_auto_start_channel(
    tmp_path: Path,
) -> None:
    """The one ordering the per-pass sweep could get wrong: it runs INSIDE the
    same pass as the auto_start policy, so both could fire for one stale row.

    The startup sweep has no such risk (it runs before the first pass and the
    pre-existing pending-start guard covers it, pinned by
    ``test_auto_start_does_not_double_enqueue_after_recovery``). Here the sweep
    queues the recovery start first, and the auto_start policy must find it
    pending and stay quiet -- one operator-visible start, not two.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    store.write_state(_row("ON_AIR", pid=4321, age_seconds=3600))
    pid_is_dead, orphan_probe = _table_seams({})  # the encoder is gone
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )
    service = _automation(store, daemon)

    service.run_once(now=datetime.now(UTC))

    assert daemon.starts == ["gov"], (
        "the per-pass sweep and the auto_start policy must produce ONE start for "
        f"one stale claim; got {daemon.starts}"
    )
    assert store.peek_pending_commands("gov") == []
    service.run_once(now=datetime.now(UTC))
    assert daemon.starts == ["gov"], f"no duplicate on a later pass: {daemon.starts}"


def test_two_service_restarts_each_queue_exactly_one_start(tmp_path: Path) -> None:
    """RED (U06): two consecutive restarts, each recovering once."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=True))
    pid_is_dead, orphan_probe = _table_seams({})

    # --- restart 1: the machine came back with a stale ON_AIR claim ---
    store.write_state(_row("ON_AIR", pid=111111, age_seconds=3600))
    first = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )
    assert first.reconcile_stale_state() == ["gov"]
    first_starts = store.pop_pending_commands("gov")
    assert [c.action for c in first_starts] == ["start"]

    # --- the channel came back on air under a NEW pid, then the machine died again ---
    store.write_state(_row("ON_AIR", pid=222222, age_seconds=3600))
    second = _RecordingDaemon(  # a NEW daemon instance: the first one is gone
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )
    assert second.reconcile_stale_state() == ["gov"]
    assert second.reconcile_stale_state() == [], "one start per restart"
    second_starts = store.pop_pending_commands("gov")
    assert len(second_starts) == 1, f"expected one start per restart, got {second_starts}"
    assert second_starts[0].command_id != first_starts[0].command_id, (
        "each restart must mint a fresh command id -- a repeated id is skipped "
        "forever by the SQL store and the channel stays dark"
    )


def test_later_tick_leaves_a_pidless_claim_alone(tmp_path: Path) -> None:
    """The deliberate limit of gap 1, pinned through a real automation pass.

    On a LATER tick a pid-less STARTING row can be this daemon's OWN in-flight
    work: ``_relaunch_after_crash``'s deferred (back-off) branch persists exactly
    this shape -- STARTING, no pid, no tracked process -- for the whole crash-loop
    cooldown, and ``_service_backoff_relaunch`` is what services it. Clearing the
    row in the per-pass sweep would replace that pacing with an immediate start,
    so the sweep must only ever reconcile pid-BEARING rows.
    """
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("STARTING", pid=None, age_seconds=5))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)
    service = _automation(store, daemon)

    service.run_once(now=datetime.now(UTC))

    assert daemon.starts == [], "a pid-less claim must never be started by a pass"
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "STARTING", (
        "the per-pass sweep must not clear a pid-less claim -- the crash-relaunch "
        "back-off owns that row"
    )


# --- R9 (rc.6, 2026-11-13): a channel persisted on FALLBACK_SLATE ------------
#
# Channel "government" had a dead live source, so the daemon forced
# FALLBACK_SLATE after its crash-loop streak. The station was then upgraded
# (service restart) and NO worker started for the channel at all: no encoder,
# no HLS segments, dark air while /health read healthy. FALLBACK_SLATE was
# treated as terminal/deliberate, but the daemon writes it itself -- it is the
# state that REPLACES dead air, not an operator's off-air intent -- and the
# channel was not auto_start, so nothing else ever started it again.


def test_startup_recovers_a_fallback_slate_row_whose_encoder_pid_is_dead(
    tmp_path: Path,
) -> None:
    """R9: persisted FALLBACK_SLATE + dead pid on a non-auto_start channel."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("FALLBACK_SLATE", pid=4321, age_seconds=3600))
    pid_is_dead, orphan_probe = _table_seams({})  # the slate encoder is gone
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )

    assert daemon.reconcile_stale_state() == ["gov"], (
        "a FALLBACK_SLATE row whose encoder is gone must be recovered on startup; "
        "otherwise a non-auto_start channel stays dark forever after an upgrade"
    )
    row = store.read_state("gov")
    assert row is not None and row.state == "STOPPED"
    pending = store.peek_pending_commands("gov")
    assert [c.action for c in pending] == ["start"]
    assert pending[0].issued_by == "restart-recovery"
    assert restart_recovery_previous_state(pending[0]) == "FALLBACK_SLATE"

    # The recovery start dispatches as slate-first from FALLBACK_SLATE: the
    # slate airs at once and the program follows through the hand-off.
    daemon.process_once("gov")
    assert daemon.starts == ["gov"]
    assert daemon.start_kwargs == [{"previous_state": "FALLBACK_SLATE", "slate_first": True}]

    # Exactly once: the row is STOPPED now, so a re-run queues nothing.
    assert daemon.reconcile_stale_state() == []
    assert store.peek_pending_commands("gov") == []


def test_startup_recovers_a_pidless_fallback_slate_row(tmp_path: Path) -> None:
    """R9: the same with no pid at all (e.g. a slate that could not launch).

    A just-started daemon provably owns no encoder, so the claim belongs to a
    predecessor and the channel must get a recovery start."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("FALLBACK_SLATE", pid=None))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    assert daemon.reconcile_stale_state() == ["gov"]
    pending = store.peek_pending_commands("gov")
    assert [c.action for c in pending] == ["start"]
    assert restart_recovery_previous_state(pending[0]) == "FALLBACK_SLATE"


@pytest.mark.parametrize("state", ["STOPPED", "DRAINING"])
@pytest.mark.parametrize("pid", [4321, None])
def test_operator_off_air_states_are_still_never_recovered(
    tmp_path: Path, state: EgressState, pid: int | None
) -> None:
    """The R9 fix's boundary: STOPPED and DRAINING are operator intent and stay
    untouched by BOTH sweeps, with a dead pid or with none."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row(state, pid=pid))
    pid_is_dead, orphan_probe = _table_seams({})
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )

    assert daemon.reconcile_stale_state() == []
    assert daemon.reconcile_dead_encoders() == []
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == state


def test_live_fallback_slate_encoder_is_left_alone(tmp_path: Path) -> None:
    """A FALLBACK_SLATE row whose encoder is still OURS (created before the
    row's last write) is never touched by either sweep."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("FALLBACK_SLATE", pid=4321, age_seconds=300))
    pid_is_dead, orphan_probe = _table_seams(
        {4321: OrphanInfo(name="python.exe", created_at=time.time() - 3600.0)}
    )
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )

    assert daemon.reconcile_stale_state() == []
    assert daemon.reconcile_dead_encoders() == []
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "FALLBACK_SLATE"


def test_fallback_slate_encoder_that_dies_later_is_recovered_by_a_pass(
    tmp_path: Path,
) -> None:
    """The per-pass sweep covers FALLBACK_SLATE too: a slate encoder alive at
    startup that dies afterwards on a non-auto_start channel is recovered."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("FALLBACK_SLATE", pid=4321, age_seconds=60))
    table = {4321: OrphanInfo(name="python.exe", created_at=time.time() - 3600.0)}
    pid_is_dead, orphan_probe = _table_seams(table)
    daemon = _RecordingDaemon(
        store, work_dir=tmp_path, pid_is_dead=pid_is_dead, orphan_probe=orphan_probe
    )
    service = _automation(store, daemon)
    now = datetime.now(UTC)

    assert daemon.reconcile_stale_state() == []
    service.run_once(now=now)
    assert daemon.starts == []

    table.clear()
    service.run_once(now=now)
    assert daemon.starts == ["gov"]
    assert daemon.start_kwargs == [{"previous_state": "FALLBACK_SLATE", "slate_first": True}]


def test_later_tick_leaves_a_pidless_fallback_slate_claim_alone(tmp_path: Path) -> None:
    """On a running daemon a pid-less FALLBACK_SLATE row can be its own work
    (a channel with no slate configured writes FALLBACK_SLATE with no pid), so
    the per-pass sweep must not restart it -- only the startup sweep may."""
    store = InMemoryEgressStore()
    store.upsert_config(_config(auto_start=False))
    store.write_state(_row("FALLBACK_SLATE", pid=None, age_seconds=5))
    daemon = _RecordingDaemon(store, work_dir=tmp_path)

    assert daemon.reconcile_dead_encoders() == []
    assert store.peek_pending_commands("gov") == []
    row = store.read_state("gov")
    assert row is not None and row.state == "FALLBACK_SLATE"
