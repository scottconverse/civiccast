# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""0089 adds honest, unverified guest media-control request metadata."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
REVISION = "0089_contribution_media_control_requests"
PREVIOUS = "0088_live_caption_health_alert_rules"


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_0089_is_linear_head() -> None:
    script = ScriptDirectory.from_config(_cfg("sqlite://"))
    assert script.get_heads() == ["0099_audio_description_model"]
    assert script.get_revision(REVISION).down_revision == PREVIOUS


def test_existing_lifecycle_rows_backfill_unknown_without_claiming_media_state(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path / 'm0089.sqlite'}"
    cfg = _cfg(url)
    command.upgrade(cfg, PREVIOUS)
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO remote_guest_sessions "
                    "(session_id, room_id, invite_id, guest_display_name, state, "
                    "connection_quality, proof_boundary) "
                    "VALUES ('guest-1', 'room-1', 'invite-1', 'Guest', 'on_air', "
                    "'good', 'legacy fixture')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT state, media_control_state, media_control_action, "
                    "media_control_requested_at FROM remote_guest_sessions "
                    "WHERE session_id = 'guest-1'"
                )
            ).one()
        assert tuple(row) == ("on_air", "unknown", None, None)
        assert {col["name"] for col in inspect(engine).get_columns("remote_guest_sessions")} >= {
            "media_control_state",
            "media_control_action",
            "media_control_requested_at",
        }
    finally:
        engine.dispose()
