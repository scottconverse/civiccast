# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Add the operator's station-logo switch to egress configs (issue #348).

``graphics_overlay_logo_enabled`` turns the channel's uploaded station logo
(the fixed file the service stores under
``<egress work dir>/<channel_id>/branding/logo.png``) on over the program
picture. It defaults off so an existing channel's persisted config -- and the
playout graph built from it -- is unaffected until an operator opts in.

Revision numbers are repo-global -- parent on the single current head
(``0099_egress_health_sample_index``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0100_egress_graphics_overlay_logo"
down_revision = "0099_egress_health_sample_index"
branch_labels = None
depends_on = None


def _use_schema() -> bool:
    return op.get_bind().dialect.name != "sqlite"


def upgrade() -> None:
    schema = "civiccast" if _use_schema() else None
    op.add_column(
        "egress_configs",
        sa.Column(
            "graphics_overlay_logo_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = "civiccast" if _use_schema() else None
    op.drop_column("egress_configs", "graphics_overlay_logo_enabled", schema=schema)
