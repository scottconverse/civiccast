# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Index egress health samples by channel and time (#343).

``/health`` and the restart-loop check read the newest samples per channel.
Without this index each read scanned the whole append-only table.
"""

from alembic import op

revision = "0099_egress_health_sample_index"
down_revision = "0098_channel_repeat_schedule"
branch_labels = None
depends_on = None

_INDEX = "ix_egress_health_samples_channel_sampled"


def upgrade() -> None:
    schema = "civiccast" if op.get_bind().dialect.name != "sqlite" else None
    op.create_index(
        _INDEX,
        "egress_health_samples",
        ["channel_id", "sampled_at"],
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = "civiccast" if op.get_bind().dialect.name != "sqlite" else None
    op.drop_index(_INDEX, table_name="egress_health_samples", schema=schema, if_exists=True)
