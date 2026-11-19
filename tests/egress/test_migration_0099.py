# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Migration 0099 adds the egress health sample index and keeps the rows (#343)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

_INDEX = "ix_egress_health_samples_channel_sampled"


def _index_names(engine: object) -> set[str]:
    return {ix["name"] for ix in inspect(engine).get_indexes("egress_health_samples")}  # type: ignore[arg-type]


def test_health_sample_index_upgrade_and_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm0099.sqlite'}"
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0098_channel_repeat_schedule")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO egress_health_samples (channel_id, sampled_at, state, "
                    "sink_connected_json, dropped_frames, seconds_on_air, caption_status) "
                    "VALUES ('ch1', '2026-11-01 00:00:00', 'ON_AIR', '{}', 0, 5, 'not-verified')"
                )
            )
        assert _INDEX not in _index_names(engine)
        command.upgrade(cfg, "0099_egress_health_sample_index")
        indexes = {
            ix["name"]: ix["column_names"]
            for ix in inspect(engine).get_indexes("egress_health_samples")
        }
        assert indexes[_INDEX] == ["channel_id", "sampled_at"]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM egress_health_samples")).scalar() == 1
        command.downgrade(cfg, "0098_channel_repeat_schedule")
        assert _INDEX not in _index_names(engine)
    finally:
        engine.dispose()
