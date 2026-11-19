# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import civiccast.egress.models
import civiccast.egress.store as egress_store_module
import civiccast.schedule.models  # noqa: F401
from civiccast.db import Base, bind_engine, reset_engine
from civiccast.egress.models import (
    EgressCaptionProofSample,
    EgressCommand,
    EgressCommandDb,
    EgressConfig,
    EgressHealthSample,
    EgressProofEvent,
    EgressSinkSpec,
    EgressStateRow,
)
from civiccast.egress.store import PostgresEgressStore


def _strip_tz(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def test_repeat_intent_is_persistent_and_independent_of_auto_start(store, monkeypatch):
    monkeypatch.setenv("CIVICCAST_SCHEDULE_LOOP", "1")
    config = _config().model_copy(update={"repeat_schedule": True, "auto_start": False})
    store.upsert_config(config)
    assert store.get_config("gov").repeat_schedule is True
    assert store.get_config("gov").auto_start is False
    store.upsert_config(config.model_copy(update={"repeat_schedule": False, "auto_start": True}))
    assert store.get_config("gov").repeat_schedule is False
    assert store.get_config("gov").auto_start is True


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine("sqlite:///:memory:", future=True)
    bind_engine(eng)
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        reset_engine()
        eng.dispose()


@pytest.fixture
def store(engine: Engine) -> PostgresEgressStore:
    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=engine) as session:
            yield session

    return PostgresEgressStore(factory)


def _config(*, channel_id: str = "gov", uri: str = "srt://headend.example:9000") -> EgressConfig:
    return EgressConfig(
        channel_id=channel_id,
        enabled=True,
        slate_message="CivicCast is preparing the channel.",
        loudness_target_lufs=-24.0,
        sinks=[
            EgressSinkSpec(
                kind="srt",
                label="Headend",
                uri=uri,
                secret_ref="EGRESS_SRT_PASSPHRASE",
                latency_ms=1500,
            )
        ],
    )


def test_command_history_prunes_in_indexed_batches_on_idle_runtime_poll(
    store: PostgresEgressStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress_store_module, "MAX_COMMAND_HISTORY_PER_CHANNEL", 2)
    monkeypatch.setattr(egress_store_module, "COMMAND_HISTORY_TRIM_BATCH_SIZE", 1)
    now = datetime.now(UTC)
    store.enqueue_commands(
        [
            EgressCommand(
                channel_id="gov",
                action="start",
                issued_at=now + timedelta(seconds=index),
                issued_by="test",
                command_id=f"consumed-{index}",
            )
            for index in range(4)
        ]
        + [
            EgressCommand(
                channel_id="other",
                action="start",
                issued_at=now,
                issued_by="test",
                command_id="still-pending",
            )
        ]
    )

    drained = store.pop_pending_commands("gov")
    assert len(drained) == 4
    # The first pass removes only one old consumed row even though two are over
    # the cap. A later regular daemon poll reaps the remaining excess without
    # needing a new command, and never touches another channel's pending intent.
    first_pass = store.command_storage_usage()
    assert first_pass == {"row_count": 4, "pending_count": 1, "storage_bytes": None}
    assert store.pop_pending_commands("gov") == []
    after_idle_poll = store.command_storage_usage()
    assert after_idle_poll == {"row_count": 3, "pending_count": 1, "storage_bytes": None}

    with store._session_factory() as session:
        pending = session.execute(
            select(EgressCommandDb.command_id).where(EgressCommandDb.command_id == "still-pending")
        ).scalar_one()
    assert pending == "still-pending"


def test_postgres_egress_store_upserts_config_and_replaces_sinks(
    store: PostgresEgressStore,
) -> None:
    store.upsert_config(_config())

    first = store.get_config("gov")

    assert first is not None
    assert first.loudness_target_lufs == -24.0
    assert first.sinks[0].label == "Headend"
    assert first.sinks[0].secret_ref == "EGRESS_SRT_PASSPHRASE"

    store.upsert_config(
        EgressConfig(
            channel_id="gov",
            enabled=False,
            slate_message="CivicCast is offline.",
            sinks=[EgressSinkSpec(kind="file", label="Proof", uri="build/proof.ts")],
        )
    )

    second = store.get_config("gov")

    assert second is not None
    assert second.enabled is False
    assert [sink.label for sink in second.sinks] == ["Proof"]


def test_postgres_egress_store_round_trips_ndi_relay_name(
    store: PostgresEgressStore,
) -> None:
    # Issue #116: the NDI relay intent is durable; NULL means no NDI output.
    config = _config().model_copy(update={"ndi_relay_name": "CivicCast Gov"})
    store.upsert_config(config)
    loaded = store.get_config("gov")
    assert loaded is not None
    assert loaded.ndi_relay_name == "CivicCast Gov"

    store.upsert_config(loaded.model_copy(update={"ndi_relay_name": None}))
    cleared = store.get_config("gov")
    assert cleared is not None
    assert cleared.ndi_relay_name is None


def test_postgres_egress_store_round_trips_sdi_relay_device(
    store: PostgresEgressStore,
) -> None:
    # Issue #117: the SDI relay intent is durable; NULL means no SDI output.
    config = _config().model_copy(update={"sdi_relay_device": "DeckLink Mini Monitor 4K"})
    store.upsert_config(config)
    loaded = store.get_config("gov")
    assert loaded is not None
    assert loaded.sdi_relay_device == "DeckLink Mini Monitor 4K"

    store.upsert_config(loaded.model_copy(update={"sdi_relay_device": None}))
    cleared = store.get_config("gov")
    assert cleared is not None
    assert cleared.sdi_relay_device is None


def test_postgres_egress_store_round_trips_per_sink_loudness(
    store: PostgresEgressStore,
) -> None:
    # S11b: each sink's loudness regime / explicit target / tolerance and the
    # gap-B tone-strip flag are durable per sink; a plain ``inherit`` sink reads
    # back at the model defaults (so pre-0049 rows are unaffected).
    config = _config().model_copy(
        update={
            "sinks": [
                EgressSinkSpec(
                    kind="udp-ts",
                    label="Cable headend",
                    uri="udp://239.255.0.1:5000",
                    loudness_regime="atsc-a85",
                    eas_tone_strip_enabled=False,
                ),
                EgressSinkSpec(
                    kind="srt",
                    label="CDN",
                    uri="srt://cdn.example:9000",
                    loudness_regime="streaming",
                    loudness_target_lufs=-15.0,
                    loudness_tolerance_lufs=1.5,
                ),
                EgressSinkSpec(kind="file", label="Proof", uri="build/proof.ts"),
            ]
        }
    )
    store.upsert_config(config)

    loaded = store.get_config("gov")
    assert loaded is not None
    cable, cdn, proof = loaded.sinks
    assert cable.loudness_regime == "atsc-a85"
    assert cable.loudness_target_lufs is None
    assert cable.eas_tone_strip_enabled is False
    assert cdn.loudness_regime == "streaming"
    assert cdn.loudness_target_lufs == -15.0
    assert cdn.loudness_tolerance_lufs == 1.5
    assert cdn.eas_tone_strip_enabled is True
    # The untouched sink reads back at the model defaults.
    assert proof.loudness_regime == "inherit"
    assert proof.loudness_target_lufs is None
    assert proof.loudness_tolerance_lufs is None
    assert proof.eas_tone_strip_enabled is True


def test_postgres_egress_store_caption_proof_round_trip(
    store: PostgresEgressStore,
) -> None:
    # S11a: caption decode-back proofs persist; latest reflects the newest sample,
    # and recent() returns newest-first.
    boundary = "egress-caption-embed-to-emitted-stream-decode-back"
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    store.append_caption_proof_sample(
        EgressCaptionProofSample(
            channel_id="gov",
            sampled_at=t0,
            status="FAIL",
            caption_status="not-verified",
            mode="cea-708",
            decoder_name="ffmpeg-readeia608",
            expected_cue_count=2,
            decoded_cue_count=0,
            matched_cue_count=0,
            proof_boundary=boundary,
            blocker="EGRESS_CAPTION_DECODE_BACK_MISMATCH",
        )
    )
    store.append_caption_proof_sample(
        EgressCaptionProofSample(
            channel_id="gov",
            sampled_at=t0 + timedelta(seconds=30),
            status="PASS",
            caption_status="on",
            mode="cea-708",
            decoder_name="ffmpeg-readeia608",
            expected_cue_count=2,
            decoded_cue_count=2,
            matched_cue_count=2,
            max_timing_delta_seconds=0.1,
            proof_boundary=boundary,
        )
    )

    latest = store.latest_caption_proof_sample("gov")
    assert latest is not None
    assert latest.status == "PASS"
    assert latest.caption_status == "on"
    assert latest.matched_cue_count == 2

    recent = store.recent_caption_proof_samples("gov", 5)
    assert [s.status for s in recent] == ["PASS", "FAIL"]
    assert store.latest_caption_proof_sample("other-channel") is None


def test_postgres_egress_store_lists_configs_in_channel_order(
    store: PostgresEgressStore,
) -> None:
    store.upsert_config(_config(channel_id="schools"))
    store.upsert_config(_config(channel_id="gov"))

    configs = store.list_configs()

    assert [config.channel_id for config in configs] == ["gov", "schools"]
    assert [config.sinks[0].label for config in configs] == ["Headend", "Headend"]


def test_postgres_egress_store_commands_are_idempotent_and_consumed(
    store: PostgresEgressStore,
) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    later = EgressCommand(
        channel_id="gov",
        action="stop",
        issued_at=now + timedelta(seconds=1),
        issued_by="operator",
        command_id="cmd-2",
    )
    earlier = EgressCommand(
        channel_id="gov",
        action="start",
        issued_at=now,
        issued_by="operator",
        command_id="cmd-1",
    )

    store.enqueue_command(later)
    store.enqueue_command(earlier)
    store.enqueue_command(earlier)

    # PR #212 round 2: peeking reports the same ordered queue without
    # consuming it (the daemon's slate-EOS relaunch yields to a queued stop).
    assert [cmd.command_id for cmd in store.peek_pending_commands("gov")] == ["cmd-1", "cmd-2"]
    assert [cmd.command_id for cmd in store.peek_pending_commands("gov")] == ["cmd-1", "cmd-2"]
    assert store.peek_pending_commands("other") == []

    popped = store.pop_pending_commands("gov")

    assert [cmd.command_id for cmd in popped] == ["cmd-1", "cmd-2"]
    assert store.peek_pending_commands("gov") == []
    assert store.pop_pending_commands("gov") == []
    store.enqueue_command(earlier)
    assert store.pop_pending_commands("gov") == []


def test_postgres_egress_store_enqueue_commands_is_one_commit(engine: Engine) -> None:
    # Review round 3 delta, MINOR 1: the headend-preset route's stop + start
    # pair must be one durable write. Count commits through the session
    # factory the store is given; the batch must produce exactly one.
    commits: list[int] = []

    class _CountingSession(Session):
        def commit(self) -> None:
            commits.append(1)
            super().commit()

    @contextmanager
    def factory() -> Iterator[Session]:
        with _CountingSession(bind=engine) as session:
            yield session

    store = PostgresEgressStore(factory)
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    pair = [
        EgressCommand(
            channel_id="gov",
            action=action,  # type: ignore[arg-type]
            issued_at=now + timedelta(microseconds=offset),
            issued_by="operator",
            command_id=f"restart-{offset}-{action}",
        )
        for offset, action in enumerate(("stop", "start"))
    ]

    store.enqueue_commands(pair)

    assert len(commits) == 1
    assert [cmd.action for cmd in store.pop_pending_commands("gov")] == ["stop", "start"]
    # Idempotent like the single path: a replayed batch adds nothing.
    store.enqueue_commands(pair)
    assert store.pop_pending_commands("gov") == []


def test_in_memory_egress_store_enqueue_commands_keeps_order_and_dedupes() -> None:
    from civiccast.egress.store import InMemoryEgressStore

    store = InMemoryEgressStore()
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    pair = [
        EgressCommand(
            channel_id="gov",
            action=action,  # type: ignore[arg-type]
            issued_at=now + timedelta(microseconds=offset),
            issued_by="operator",
            command_id=f"restart-{offset}-{action}",
        )
        for offset, action in enumerate(("stop", "start"))
    ]

    store.enqueue_commands(pair)
    store.enqueue_commands(pair)

    assert [cmd.action for cmd in store.pop_pending_commands("gov")] == ["stop", "start"]
    assert store.pop_pending_commands("gov") == []


def test_in_memory_egress_store_enqueue_commands_is_one_list_swap() -> None:
    """Review round 4 delta, MINOR 3: the "one list swap in memory" claim.

    A reader interleaved between the batch's items must see NONE of it: the
    batch becomes visible in one assignment, never as a prefix. The batch is
    a sequence whose iteration peeks at the store between the ``stop`` and
    the ``start``; a per-item ``append`` shows the reader a lone ``stop``.
    """
    from civiccast.egress.store import InMemoryEgressStore

    store = InMemoryEgressStore()
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    pair = [
        EgressCommand(
            channel_id="gov",
            action=action,  # type: ignore[arg-type]
            issued_at=now + timedelta(microseconds=offset),
            issued_by="operator",
            command_id=f"restart-{offset}-{action}",
        )
        for offset, action in enumerate(("stop", "start"))
    ]
    seen_between_items: list[list[str]] = []

    class _PeekingBatch(list[EgressCommand]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            for index, cmd in enumerate(list.__iter__(self)):
                if index:
                    seen_between_items.append(
                        [c.action for c in store.peek_pending_commands("gov")]
                    )
                yield cmd

    store.enqueue_commands(_PeekingBatch(pair))

    assert seen_between_items == [[]], "a reader between the items saw a prefix of the batch"
    assert [cmd.action for cmd in store.pop_pending_commands("gov")] == ["stop", "start"]


def test_postgres_egress_store_state_and_recent_health(store: PostgresEgressStore) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    state = EgressStateRow(
        channel_id="gov",
        state="ON_AIR",
        current_source_label="Council meeting",
        current_proof_event_id="proof-1",
        updated_at=now,
        pid=4242,
    )

    store.write_state(state)
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now,
            state="ON_AIR",
            sink_connected={"Headend": True},
            encoder_fps=30.0,
            seconds_on_air=10,
            caption_status="not-verified",
        )
    )
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now + timedelta(seconds=1),
            state="ON_AIR",
            sink_connected={"Headend": True},
            encoder_fps=30.0,
            seconds_on_air=11,
            caption_status="on",
        )
    )

    got = store.read_state("gov")
    assert got is not None
    assert got.model_copy(update={"updated_at": _strip_tz(got.updated_at)}) == state.model_copy(
        update={"updated_at": _strip_tz(state.updated_at)}
    )
    assert [sample.seconds_on_air for sample in store.recent_health("gov", 2)] == [11, 10]
    assert [sample.caption_status for sample in store.recent_health("gov", 2)] == [
        "on",
        "not-verified",
    ]


def test_postgres_egress_store_trims_old_health_samples(store: PostgresEgressStore) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now - timedelta(days=8),
            state="ON_AIR",
            sink_connected={"Headend": True},
        )
    )
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=now - timedelta(days=1),
            state="ON_AIR",
            sink_connected={"Headend": True},
        )
    )

    assert store.trim_health_before(now - timedelta(days=7)) == 1

    assert [sample.sampled_at for sample in store.recent_health("gov", 10)] == [
        now.replace(tzinfo=None) - timedelta(days=1)
    ]


def test_postgres_egress_store_appends_recent_proof_events(
    store: PostgresEgressStore,
) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    older = EgressProofEvent(
        event_id="proof-1",
        observed_at=now,
        channel_id="gov",
        state="ON_AIR",
        source_label="Council meeting",
        source_path="prepared/council.ts",
        source_ref="asset-council",
        proof_boundary="civiccast-egress-handoff-boundary",
        machine_summary="Council meeting went to air.",
    )
    newer = older.model_copy(
        update={
            "event_id": "proof-2",
            "observed_at": now + timedelta(seconds=1),
            "source_label": "Station slate",
        }
    )

    store.append_proof_event(older)
    store.append_proof_event(newer)
    store.append_proof_event(older)

    events = store.recent_proof_events("gov", 2)

    assert [event.event_id for event in events] == ["proof-2", "proof-1"]
    assert [event.source_ref for event in events] == ["asset-council", "asset-council"]
    assert events[0].proof_boundary == "civiccast-egress-handoff-boundary"


def test_health_sample_schema_currency_round_trip(store: PostgresEgressStore) -> None:
    store.append_health(
        EgressHealthSample(
            channel_id="gov",
            sampled_at=datetime.now(UTC),
            state="ON_AIR",
            schema_version=7,
            proof_events_appended_since_last_sample=23,
        )
    )
    got = store.recent_health("gov", 1)[0]
    assert got.schema_version == 7
    assert got.proof_events_appended_since_last_sample == 23


def test_health_sample_defaults_round_trip(store: PostgresEgressStore) -> None:
    # an unset sample defaults schema_version to the current code version, churn to 0
    from civiccast.egress.schema_currency import current_schema_version

    store.append_health(
        EgressHealthSample(channel_id="gov", sampled_at=datetime.now(UTC), state="ON_AIR")
    )
    got = store.recent_health("gov", 1)[0]
    assert got.schema_version == current_schema_version()
    assert got.proof_events_appended_since_last_sample == 0


def _health_at(sampled_at: datetime, state: str = "ON_AIR") -> EgressHealthSample:
    return EgressHealthSample(
        channel_id="gov",
        sampled_at=sampled_at,
        state=state,  # type: ignore[arg-type]
        sink_connected={"Headend": True},
    )


def test_postgres_store_counts_health_states_in_a_window(store: PostgresEgressStore) -> None:
    """#327: restart-loop detection counts STARTING samples inside a window."""

    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    for minutes_ago in (20, 8, 4, 1):
        store.append_health(_health_at(now - timedelta(minutes=minutes_ago), "STARTING"))
    store.append_health(_health_at(now, "ON_AIR"))

    since = now - timedelta(minutes=10)
    assert store.count_health_states_since("gov", "STARTING", since) == 3
    assert store.count_health_states_since("gov", "ON_AIR", since) == 1
    assert store.count_health_states_since("other", "STARTING", since) == 0


def test_postgres_store_trims_health_past_retention_on_append(
    store: PostgresEgressStore,
) -> None:
    """#343: append trims samples older than the retention window, without a manual job."""

    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=egress_store_module.HEALTH_RETENTION_DAYS + 1)
    store.append_health(_health_at(old))
    store.append_health(_health_at(now - timedelta(days=1)))

    store.append_health(_health_at(now))

    kept = [_strip_tz(s.sampled_at) for s in store.recent_health("gov", 10)]
    assert _strip_tz(old) not in kept
    assert len(kept) == 2


def test_postgres_store_health_trim_runs_once_per_interval(
    store: PostgresEgressStore,
) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=egress_store_module.HEALTH_RETENTION_DAYS + 5)
    store.append_health(_health_at(old))
    store._health_trimmed_at = now  # a trim just ran
    store.append_health(_health_at(now + timedelta(minutes=1)))
    assert len(store.recent_health("gov", 10)) == 2  # inside the interval: no trim

    interval = timedelta(seconds=egress_store_module.HEALTH_TRIM_INTERVAL_SECONDS)
    store.append_health(_health_at(now + interval))
    assert len(store.recent_health("gov", 10)) == 2  # trim ran: ``old`` gone


def test_postgres_store_health_trim_drains_a_backlog_in_batches(
    store: PostgresEgressStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A big backlog (621k rows on one station) goes in bounded batches, one per append."""

    monkeypatch.setattr(egress_store_module, "HEALTH_TRIM_BATCH_SIZE", 2)
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=egress_store_module.HEALTH_RETENTION_DAYS + 1)
    store._health_trimmed_at = now  # keep the backlog in place while it is written
    for i in range(5):
        store.append_health(_health_at(old - timedelta(minutes=i)))
    store._health_trimmed_at = None

    store.append_health(_health_at(now))
    assert len(store.recent_health("gov", 10)) == 4  # 5 old - 2 + 1 new
    store.append_health(_health_at(now + timedelta(seconds=10)))
    assert len(store.recent_health("gov", 10)) == 3  # full batch kept the trim due
    store.append_health(_health_at(now + timedelta(seconds=20)))
    assert len(store.recent_health("gov", 10)) == 3  # last old row gone; batch not full


def test_health_trim_failure_never_fails_the_append(
    store: PostgresEgressStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(cutoff: datetime, batch_size: int) -> int:
        raise RuntimeError("disk busy")

    monkeypatch.setattr(store, "trim_health_batch_before", _boom)
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)

    store.append_health(_health_at(now))

    assert len(store.recent_health("gov", 10)) == 1


def test_in_memory_store_counts_and_trims_health() -> None:
    from civiccast.egress.store import InMemoryEgressStore

    store = InMemoryEgressStore()
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    store.append_health(_health_at(now - timedelta(days=40), "STARTING"))
    store.append_health(_health_at(now - timedelta(minutes=5), "STARTING"))
    store.append_health(_health_at(now))

    assert store.count_health_states_since("gov", "STARTING", now - timedelta(days=60)) == 1
    assert len(store.recent_health("gov", 10)) == 2
