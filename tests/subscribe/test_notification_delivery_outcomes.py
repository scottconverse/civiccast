# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Migration 0085 + the durable notification-delivery outcome store (WP-05).

The schema is the load-bearing part of the fix: the UNIQUE constraint on the
logical delivery identity is what makes two concurrent approvals unable to
double-send a resident. ``create_all`` (the ORM) would pass even if the
migration were broken, so these tests drive the real alembic upgrade and
downgrade, then exercise the store against the schema the migration produced.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import civiccast.subscribe.models  # noqa: F401 -- register the WP-05 tables on Base.metadata
from civiccast.db import Base
from civiccast.subscribe.outcome_store import (
    DELIVERY_LEASE_SECONDS,
    InMemoryNotificationDeliveryStore,
    PostgresNotificationDeliveryStore,
    logical_delivery_key,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

_WP05_TABLES = {"notification_delivery_outcomes", "notification_delivery_attempts"}
_PRIOR_TABLE = "subscription_webhook_retries"
_REVISION = "0092_notification_delivery_outcomes"
_PARENT = "0091_programlog_local_anchor"
_CURRENT_HEAD = "0099_audio_description_model"


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _tables(url: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_alembic_graph_still_has_exactly_one_head() -> None:
    """Notification outcomes must not fork the graph used by `upgrade head`."""

    heads = tuple(ScriptDirectory.from_config(_cfg("sqlite://")).get_heads())
    assert heads == (_CURRENT_HEAD,), (
        f"expected the single head to be {_CURRENT_HEAD}; got {sorted(heads)}. "
        "Re-parent the newest migration onto the single current head before "
        "merging -- two heads break `alembic upgrade head`, which is the "
        "installer's own storage-setup path."
    )


def test_0085_parents_on_the_caption_language_migration() -> None:
    script = ScriptDirectory.from_config(_cfg("sqlite://"))
    revision = script.get_revision(_REVISION)
    assert revision.down_revision == _PARENT


def test_upgrade_creates_both_delivery_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm0085.sqlite'}"
    command.upgrade(_cfg(url), "head")

    missing = _WP05_TABLES - _tables(url)
    assert not missing, f"0085 did not create tables: {missing}"

    # The idempotency guard has to be in the schema the MIGRATION produces,
    # not only in the ORM metadata create_all builds.
    engine = create_engine(url, future=True)
    try:
        constraints = inspect(engine).get_unique_constraints("notification_delivery_outcomes")
    finally:
        engine.dispose()
    by_name = {constraint["name"]: constraint["column_names"] for constraint in constraints}
    assert by_name.get("notification_delivery_outcomes_logical_key") == [
        "publication_id",
        "subscription_id",
        "target_type",
        "target_id",
        "transport",
    ]


def test_downgrade_removes_them_and_leaves_prior_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm0085_down.sqlite'}"
    cfg = _cfg(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _PARENT)

    tables = _tables(url)
    assert not (_WP05_TABLES & tables), f"0085 downgrade left tables: {_WP05_TABLES & tables}"
    assert _PRIOR_TABLE in tables, "0085 downgrade removed a prior migration's table"


def test_upgrade_backfills_nothing_because_no_receipt_ever_existed(tmp_path: Path) -> None:
    """No prior release wrote a delivery receipt, so there is nothing to backfill.

    This is the assertion behind the migration's backfill note: the tables come
    up EMPTY rather than seeded with invented receipts for historical publish
    runs. Publish reads that emptiness as ``unverified``.
    """

    url = f"sqlite:///{tmp_path / 'm0085_empty.sqlite'}"
    command.upgrade(_cfg(url), "head")

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            outcomes = conn.execute(
                text("SELECT COUNT(*) FROM notification_delivery_outcomes")
            ).scalar_one()
            attempts = conn.execute(
                text("SELECT COUNT(*) FROM notification_delivery_attempts")
            ).scalar_one()
    finally:
        engine.dispose()
    assert (outcomes, attempts) == (0, 0)


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """A schema-qualified SQLite engine, matching this repo's store-test shape.

    The ORM addresses ``civiccast.<table>``, which SQLite resolves through the
    ``ATTACH DATABASE ':memory:' AS civiccast`` connect hook in
    ``civiccast.db.base``. The migration tests above drive the real alembic
    upgrade against a file database (where SQLite gets unqualified names); this
    fixture is the store-level counterpart, the same split every other
    ``Postgres*Store`` test in this repo uses.
    """

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def store(sqlite_engine: Engine) -> PostgresNotificationDeliveryStore:
    @contextmanager
    def session_factory() -> Iterator[Session]:
        session = Session(bind=sqlite_engine)
        try:
            yield session
        finally:
            session.close()

    return PostgresNotificationDeliveryStore(session_factory)


_CLAIM = {
    "publication_id": "pub:meeting-42",
    "asset_id": "meeting-42",
    "subscription_id": "sub-abc",
    "target_type": "channel",
    "target_id": "government",
    "transport": "email",
}


def test_logical_key_excludes_the_attempt_number() -> None:
    """The key IS the duplicate-send guard, so attempts must collide on it."""

    first = logical_delivery_key(
        publication_id="pub:a",
        subscription_id="sub-1",
        target_type="channel",
        target_id="government",
        transport="email",
    )
    second = logical_delivery_key(
        publication_id="pub:a",
        subscription_id="sub-1",
        target_type="channel",
        target_id="government",
        transport="email",
    )
    other_transport = logical_delivery_key(
        publication_id="pub:a",
        subscription_id="sub-1",
        target_type="channel",
        target_id="government",
        transport="webhook",
    )
    assert first == second
    assert first != other_transport


def test_second_claim_returns_the_same_row_and_never_a_duplicate(
    store: PostgresNotificationDeliveryStore,
) -> None:
    first = store.claim(**_CLAIM)
    second = store.claim(**_CLAIM)

    assert first.created is True
    assert second.created is False
    assert second.record.delivery_key == first.record.delivery_key
    assert len(store.list_for_publication("pub:meeting-42")) == 1


def test_unique_constraint_rejects_a_second_row_for_the_same_logical_delivery(
    store: PostgresNotificationDeliveryStore, sqlite_engine: Engine
) -> None:
    """The guard is in the SCHEMA, not in memory.

    Written straight through SQL with a different primary key, so it bypasses
    the store's own read-before-insert entirely -- which is what a second
    process racing the first would effectively do.
    """

    from sqlalchemy.exc import IntegrityError

    store.claim(**_CLAIM)
    with sqlite_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO civiccast.notification_delivery_outcomes ("
                "delivery_key, publication_id, asset_id, subscription_id, "
                "target_type, target_id, transport, outcome, attempts, detail, "
                "created_at, updated_at) VALUES ("
                "'ndk-a-different-primary-key', :publication_id, :asset_id, "
                ":subscription_id, :target_type, :target_id, :transport, "
                "'pending', 0, '', :now, :now)"
            ),
            {**_CLAIM, "now": datetime.now(UTC).isoformat()},
        )


def test_attempts_are_numbered_beneath_one_logical_delivery(
    store: PostgresNotificationDeliveryStore,
) -> None:
    claimed = store.claim(**_CLAIM)
    key = claimed.record.delivery_key

    store.record_attempt(delivery_key=key, outcome="failed", error_code="SMTPDataError")
    store.record_attempt(delivery_key=key, outcome="queued", retry_id="swr_1")
    final = store.record_attempt(delivery_key=key, outcome="sent")

    attempts = store.list_attempts(key)
    assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3]
    assert [attempt.outcome for attempt in attempts] == ["failed", "queued", "sent"]
    assert final.attempts == 3
    assert final.outcome == "sent"
    assert final.succeeded_at is not None
    assert final.first_attempted_at is not None
    assert len(store.list_for_publication("pub:meeting-42")) == 1


def test_durable_webhook_retry_result_updates_only_its_queued_receipt(
    store: PostgresNotificationDeliveryStore,
) -> None:
    claimed = store.claim(**_CLAIM)
    key = claimed.record.delivery_key
    store.record_attempt(delivery_key=key, outcome="queued", retry_id="swr_1")
    assert (
        store.record_webhook_retry_result(retry_id="swr_other", outcome="sent", detail="accepted")
        is None
    )
    delivered = store.record_webhook_retry_result(
        retry_id="swr_1", outcome="sent", detail="accepted"
    )
    assert delivered is not None and delivered.outcome == "sent"
    assert store.claim(**_CLAIM, allow_reattempt=True).granted is False
    assert [attempt.outcome for attempt in store.list_attempts(key)] == ["queued", "sent"]


def test_in_memory_store_matches_the_durable_store_semantics() -> None:
    """The ephemeral fallback must not have looser rules than the real store."""

    store = InMemoryNotificationDeliveryStore()

    first = store.claim(**_CLAIM)
    store.record_attempt(delivery_key=first.record.delivery_key, outcome="sent")
    second = store.claim(**_CLAIM)

    assert first.created is True
    assert second.created is False
    assert second.already_sent is True
    assert len(store.list_for_publication("pub:meeting-42")) == 1


def test_stored_rows_carry_no_recipient_handle(
    store: PostgresNotificationDeliveryStore,
) -> None:
    claimed = store.claim(**_CLAIM)
    store.record_attempt(
        delivery_key=claimed.record.delivery_key,
        outcome="failed",
        error_code="SMTPRecipientsRefused",
        detail="Mail provider refused the address.",
    )

    rendered = store.get(claimed.record.delivery_key)
    assert rendered is not None
    assert "@" not in rendered.model_dump_json()


class TestDoubleSendRace:
    """B1: two interleaved claims must grant exactly one sender.

    Reproduced by the reviewer against the previous implementation, which
    inserted a ``pending`` row and then gated the send on "not already sent" --
    so both workers read the same un-sent row and both were told to send.
    """

    def test_two_interleaved_claims_grant_exactly_one_sender(
        self, store: PostgresNotificationDeliveryStore
    ) -> None:
        worker_a = store.claim(**_CLAIM)
        worker_b = store.claim(**_CLAIM)

        assert [worker_a.granted, worker_b.granted] == [True, False]
        assert [worker_a.created, worker_b.created] == [True, False]
        # Neither row is "sent" yet -- the OLD gate would have cleared both.
        assert worker_a.record.outcome == "pending"
        assert worker_b.already_sent is False
        assert len(store.list_for_publication("pub:meeting-42")) == 1

    def test_the_loser_is_still_refused_after_the_winner_succeeds(
        self, store: PostgresNotificationDeliveryStore
    ) -> None:
        winner = store.claim(**_CLAIM)
        store.record_attempt(delivery_key=winner.record.delivery_key, outcome="sent")

        loser = store.claim(**_CLAIM)

        assert loser.granted is False
        assert loser.already_sent is True

    def test_a_stale_in_flight_row_is_recoverable_once_its_lease_lapses(
        self, store: PostgresNotificationDeliveryStore
    ) -> None:
        """A process killed mid-send must not strand the recipient forever."""

        start = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
        dead_worker = store.claim(**_CLAIM, now=start)
        assert dead_worker.granted is True
        # ... and then dies without ever recording an attempt.

        still_owned = store.claim(**_CLAIM, now=start + timedelta(seconds=60))
        assert still_owned.granted is False

        after_lease = store.claim(
            **_CLAIM, now=start + timedelta(seconds=DELIVERY_LEASE_SECONDS + 1)
        )
        assert after_lease.granted is True
        assert after_lease.created is False
        assert len(store.list_for_publication("pub:meeting-42")) == 1

    def test_recording_a_result_releases_the_lease(
        self, store: PostgresNotificationDeliveryStore
    ) -> None:
        claimed = store.claim(**_CLAIM)
        after = store.record_attempt(delivery_key=claimed.record.delivery_key, outcome="failed")

        assert claimed.record.lease_expires_at is not None
        assert after.lease_expires_at is None

    def test_a_failed_row_is_reattempted_only_by_an_explicit_retry(
        self, store: PostgresNotificationDeliveryStore
    ) -> None:
        claimed = store.claim(**_CLAIM)
        store.record_attempt(delivery_key=claimed.record.delivery_key, outcome="failed")

        reapproval = store.claim(**_CLAIM)
        assert reapproval.granted is False, "a normal re-approval must stay idempotent"

        retry = store.claim(**_CLAIM, allow_reattempt=True)
        assert retry.granted is True
        assert retry.created is False

    def test_a_queued_row_remains_owned_by_the_worker_during_explicit_retry(
        self, store: PostgresNotificationDeliveryStore
    ) -> None:
        claimed = store.claim(**_CLAIM)
        store.record_attempt(
            delivery_key=claimed.record.delivery_key, outcome="queued", retry_id="swr_1"
        )

        assert store.claim(**_CLAIM).granted is False
        assert store.claim(**_CLAIM, allow_reattempt=True).granted is False

    def test_a_sent_row_is_never_reattempted_even_by_an_explicit_retry(
        self, store: PostgresNotificationDeliveryStore
    ) -> None:
        claimed = store.claim(**_CLAIM)
        store.record_attempt(delivery_key=claimed.record.delivery_key, outcome="sent")

        assert store.claim(**_CLAIM, allow_reattempt=True).granted is False

    def test_the_in_memory_store_applies_the_same_grant_rules(self) -> None:
        store = InMemoryNotificationDeliveryStore()

        first = store.claim(**_CLAIM)
        second = store.claim(**_CLAIM)

        assert [first.granted, second.granted] == [True, False]
        store.record_attempt(delivery_key=first.record.delivery_key, outcome="failed")
        assert store.claim(**_CLAIM).granted is False
        assert store.claim(**_CLAIM, allow_reattempt=True).granted is True


def test_migration_creates_the_lease_column_and_the_attempt_foreign_key(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm0085_lease.sqlite'}"
    command.upgrade(_cfg(url), "head")

    engine = create_engine(url, future=True)
    try:
        inspector = inspect(engine)
        columns = {
            column["name"] for column in inspector.get_columns("notification_delivery_outcomes")
        }
        foreign_keys = inspector.get_foreign_keys("notification_delivery_attempts")
    finally:
        engine.dispose()

    assert "lease_expires_at" in columns
    assert [key["referred_table"] for key in foreign_keys] == ["notification_delivery_outcomes"]
    assert foreign_keys[0]["constrained_columns"] == ["delivery_key"]
