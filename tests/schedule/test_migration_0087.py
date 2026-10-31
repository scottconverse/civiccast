# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""WP-08 migration 0087_retention_terms.

Drives the real migration through Alembic against a fresh sqlite fixture
DB (not ``create_all`` -- that builds the *current* ORM schema and would
never catch a broken migration file). Covers upgrade, downgrade,
round-trip, single-head, and both backfill rules (permanent -> forever,
published_at -> retention_anchor_at) against production-shaped rows
inserted with raw SQL before the migration runs, per the finalization
plan's per-work-package completion gate ("backfill of production-shaped
existing rows").
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

_PARENT_REVISION = "0086_live_source_probe_state"
_REVISION = "0087_retention_terms"
_CURRENT_HEAD = "0099_audio_description_model"

_NEW_COLUMNS = ("retention_term_unit", "retention_term_value", "retention_anchor_at")


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _seed_minimal_assets_table(url: str) -> None:
    """Insert a couple of production-shaped asset rows at the parent
    revision, before 0087 runs, so the backfill has real data to act on."""
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO assets (asset_id, title, state, retention_policy, "
                    "published_at) VALUES (:id, :title, 'validated', :policy, :pub)"
                ),
                [
                    {
                        "id": "perm-1",
                        "title": "Permanent record",
                        "policy": "permanent",
                        "pub": None,
                    },
                    {
                        "id": "published-default-1",
                        "title": "Published default-policy asset",
                        "policy": "default",
                        "pub": "2026-01-15T12:00:00+00:00",
                    },
                    {
                        "id": "unpublished-short-1",
                        "title": "Never-published short-policy asset",
                        "policy": "short",
                        "pub": None,
                    },
                ],
            )
    finally:
        engine.dispose()


def test_single_head() -> None:
    """The complete migration set must resolve to the current single head."""
    script = ScriptDirectory.from_config(_cfg("sqlite:///:memory:"))
    heads = script.get_heads()
    assert heads == [_CURRENT_HEAD], f"expected current head {_CURRENT_HEAD!r}, got {heads}"


def test_upgrade_adds_the_three_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "0087_up.db"
    url = f"sqlite:///{db_path}"
    command.upgrade(_cfg(url), _PARENT_REVISION)

    engine = create_engine(url, future=True)
    try:
        before = {c["name"] for c in inspect(engine).get_columns("assets")}
        for col in _NEW_COLUMNS:
            assert col not in before, f"{col} should not exist before 0087"
    finally:
        engine.dispose()

    command.upgrade(_cfg(url), _REVISION)

    engine = create_engine(url, future=True)
    try:
        after = {c["name"] for c in inspect(engine).get_columns("assets")}
        for col in _NEW_COLUMNS:
            assert col in after, f"{col} missing after upgrading to 0087"
    finally:
        engine.dispose()


def test_downgrade_removes_the_three_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "0087_down.db"
    url = f"sqlite:///{db_path}"
    command.upgrade(_cfg(url), _REVISION)

    command.downgrade(_cfg(url), _PARENT_REVISION)

    engine = create_engine(url, future=True)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("assets")}
        for col in _NEW_COLUMNS:
            assert col not in cols, f"{col} still present after downgrade"
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "0087_roundtrip.db"
    url = f"sqlite:///{db_path}"
    cfg = _cfg(url)

    command.upgrade(cfg, _REVISION)
    command.downgrade(cfg, _PARENT_REVISION)
    command.upgrade(cfg, _REVISION)

    engine = create_engine(url, future=True)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("assets")}
        for col in _NEW_COLUMNS:
            assert col in cols
    finally:
        engine.dispose()


def test_empty_database_creation_reaches_head(tmp_path: Path) -> None:
    """Finalization plan section 7: 'Empty database creation' gate."""
    db_path = tmp_path / "0087_empty.db"
    url = f"sqlite:///{db_path}"
    command.upgrade(_cfg(url), "head")

    engine = create_engine(url, future=True)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("assets")}
        for col in _NEW_COLUMNS:
            assert col in cols
    finally:
        engine.dispose()


def test_backfill_permanent_maps_to_forever(tmp_path: Path) -> None:
    """Backfill 1: an unambiguous permanent -> forever mapping needs no
    anchor (forever never computes a deadline from one)."""
    db_path = tmp_path / "0087_backfill_permanent.db"
    url = f"sqlite:///{db_path}"
    command.upgrade(_cfg(url), _PARENT_REVISION)
    _seed_minimal_assets_table(url)

    command.upgrade(_cfg(url), _REVISION)

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT retention_term_unit, retention_term_value, retention_anchor_at "
                    "FROM assets WHERE asset_id = 'perm-1'"
                )
            ).one()
        assert row.retention_term_unit == "forever"
        assert row.retention_term_value is None
        # No publication anchor available for this row -- backfill 2 must
        # not invent one.
        assert row.retention_anchor_at is None
    finally:
        engine.dispose()


def test_backfill_reuses_published_at_as_anchor(tmp_path: Path) -> None:
    """Backfill 2: a real published_at is a legitimate anchor source; an
    asset with none gets no anchor invented."""
    db_path = tmp_path / "0087_backfill_anchor.db"
    url = f"sqlite:///{db_path}"
    command.upgrade(_cfg(url), _PARENT_REVISION)
    _seed_minimal_assets_table(url)

    command.upgrade(_cfg(url), _REVISION)

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            published = conn.execute(
                text(
                    "SELECT retention_anchor_at, retention_term_unit FROM assets "
                    "WHERE asset_id = 'published-default-1'"
                )
            ).one()
            unpublished = conn.execute(
                text(
                    "SELECT retention_anchor_at, retention_term_unit FROM assets "
                    "WHERE asset_id = 'unpublished-short-1'"
                )
            ).one()
        assert published.retention_anchor_at is not None
        anchor = published.retention_anchor_at
        if isinstance(anchor, str):
            anchor = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
        assert anchor == datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        # `default` policy is ambiguous per the plan -- not auto-converted.
        assert published.retention_term_unit is None

        # `short` is well-known but this row was never published: no
        # anchor to backfill, and (per the plan) short is not
        # auto-converted either -- only offered as an operator prefill.
        assert unpublished.retention_anchor_at is None
        assert unpublished.retention_term_unit is None
    finally:
        engine.dispose()
