# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Migration 0086 (live-source probe state) applies, backfills, and reverses.

``create_all`` (the store fixtures) builds the schema from the ORM, so every
other WP-07 test would pass even if the MIGRATION were broken. These tests
drive the actual alembic upgrade.

The backfill assertion is the one that matters operationally: an existing
station has configured sources today, and after this migration they must land
on ``never_probed`` -- "nobody has looked yet" -- rather than inheriting a
readiness claim nothing ever verified. Upgrading straight into "everything is
ready" would reproduce the exact defect this revision exists to close.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

_REVISION = "0086_live_source_probe_state"
_PRIOR_HEAD = "0083_caption_review_language"
_COLS = {
    "probe_state",
    "probe_observed_at",
    "probe_detail",
    "probe_error_code",
    "probe_last_success_at",
    "row_version",
}


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _columns(url: str, table: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        return {col["name"] for col in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_single_head() -> None:
    """One head, reachable through this revision.

    WP-05 (0085) is parked by owner decision and will not land; 0084 never
    materialized. #131 merged 0083_caption_review_language to main, so this
    revision parents on 0083 -- the sole other head at merge time. WP-08's
    0087_retention_terms (#142) re-parented onto this revision, and 0088 adds
    the caption-health rule seed. Beta 19 advances the single head through
    notification outcomes and board audio/video to 0094. This test checks
    that this revision is still reachable and is not itself a second head.
    This assertion is what makes a future stray second head a loud failure
    rather than a silent one.
    """
    script = ScriptDirectory.from_config(_cfg("sqlite://"))
    heads = list(script.get_heads())
    assert heads == ["0099_audio_description_model"], f"expected exactly one head, found {heads!r}"
    assert script.get_revision(_REVISION) is not None, f"{_REVISION!r} must still be reachable"


def test_upgrade_adds_then_downgrade_removes_the_columns(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm0086.sqlite'}"
    cfg = _cfg(url)

    command.upgrade(cfg, "head")
    cols = _columns(url, "live_sources")
    assert cols >= _COLS, f"0086 did not add {_COLS - cols} to live_sources"

    command.downgrade(cfg, _PRIOR_HEAD)
    remaining = _columns(url, "live_sources")
    assert not (_COLS & remaining), f"0086 downgrade left {_COLS & remaining}"


def test_existing_rows_backfill_to_never_probed(tmp_path: Path) -> None:
    """A production-shaped row that predates the migration is not ready."""
    url = f"sqlite:///{tmp_path / 'm0086-backfill.sqlite'}"
    cfg = _cfg(url)

    # Bring the schema to the revision BEFORE this one and insert a source the
    # way an existing station's database already holds one.
    command.upgrade(cfg, _PRIOR_HEAD)
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO live_sources "
                    "(live_source_id, channel_id, name, source_type, endpoint_url) "
                    "VALUES ('council-encoder', 'gov-ch12', 'Council Room Encoder', "
                    "'srt', 'srt://0.0.0.0:9000?mode=listener')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT probe_state, probe_observed_at, probe_detail, probe_error_code, "
                    "probe_last_success_at, row_version FROM live_sources "
                    "WHERE live_source_id = 'council-encoder'"
                )
            ).one()
    finally:
        engine.dispose()

    probe_state, observed_at, detail, error_code, last_success, row_version = row
    assert probe_state == "never_probed"
    assert observed_at is None
    assert detail is None
    assert error_code is None
    assert last_success is None
    assert row_version == 1


def test_probe_state_check_constraint_rejects_an_unknown_value(tmp_path: Path) -> None:
    """The CHECK constraint is real, not decorative.

    ``stale`` in particular must be impossible to persist: staleness is derived
    from the observation timestamp against the readiness TTL, and a written
    "stale" would outlive the successful probe that should have cleared it.
    """
    import sqlalchemy.exc

    url = f"sqlite:///{tmp_path / 'm0086-check.sqlite'}"
    command.upgrade(_cfg(url), "head")
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            try:
                conn.execute(
                    text(
                        "INSERT INTO live_sources "
                        "(live_source_id, channel_id, name, source_type, endpoint_url, "
                        "probe_state) VALUES ('bad', 'gov-ch12', 'Bad', 'srt', "
                        "'srt://host:9000', 'stale')"
                    )
                )
            except sqlalchemy.exc.IntegrityError:
                return
        raise AssertionError("probe_state CHECK constraint did not reject 'stale'")
    finally:
        engine.dispose()


def test_empty_database_creation_reaches_head(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm0086-empty.sqlite'}"
    command.upgrade(_cfg(url), "head")
    assert _columns(url, "live_sources") >= _COLS
