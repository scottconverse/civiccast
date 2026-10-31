# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Register the independent local-only audio-description model selection."""

import sqlalchemy as sa
from alembic import op

revision = "0099_audio_description_model"
down_revision = "0098_channel_repeat_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = "civiccast" if op.get_bind().dialect.name != "sqlite" else None
    with op.batch_alter_table("feature_model_registry", schema=schema) as batch:
        batch.drop_constraint("feature_model_registry_feature_check", type_="check")
        batch.create_check_constraint(
            "feature_model_registry_feature_check",
            "feature IN ('captions', 'summary', 'translation', 'audio_description')",
        )


def downgrade() -> None:
    schema = "civiccast" if op.get_bind().dialect.name != "sqlite" else None
    table = sa.table("feature_model_registry", sa.column("feature", sa.String()), schema=schema)
    op.execute(table.delete().where(table.c.feature == "audio_description"))
    with op.batch_alter_table("feature_model_registry", schema=schema) as batch:
        batch.drop_constraint("feature_model_registry_feature_check", type_="check")
        batch.create_check_constraint(
            "feature_model_registry_feature_check",
            "feature IN ('captions', 'summary', 'translation')",
        )
