# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Live FastAPI fixture for the operator portal full-stack Playwright gate."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
os.environ.setdefault("CIVICCAST_ALLOW_DETERMINISTIC_STAFF_TOKEN", "1")
os.environ.setdefault("CIVICCAST_ACTIVITYPUB_MODE", "disabled")
os.environ.pop("DATABASE_URL", None)

from civiccast.app import create_app
from civiccast.publish.router import get_publish_store
from civiccast.publish.store import InMemoryPublishStore
from civiccast.schedule.models import StaffAssetRow
from civiccast.schedule.router import get_postgres_store


class OperatorFixtureAssetStore:
    """Small asset-store seam matching the publish router's required methods."""

    def __init__(self, assets: list[StaffAssetRow]) -> None:
        self._assets = assets

    def list_all(self) -> list[StaffAssetRow]:
        return self._assets

    def get_staff_row(self, asset_id: str) -> StaffAssetRow | None:
        return next((asset for asset in self._assets if asset.asset_id == asset_id), None)

    def mark_published(self, asset_id: str, *, published_at: datetime) -> StaffAssetRow:
        """Same contract as the real asset store's ``mark_published``.

        The publish router calls this after a Portal surface succeeds
        (``_apply_portal_visibility``). Without it the router records Portal
        as failed, which is what this full-stack gate must not hide.
        """
        for index, asset in enumerate(self._assets):
            if asset.asset_id != asset_id:
                continue
            if not asset.manifest_url:
                raise ValueError(f"Asset is not packaged: {asset_id}")
            published = asset.model_copy(update={"published_at": published_at})
            self._assets[index] = published
            return published
        raise ValueError(f"Asset not found: {asset_id}")


asset_store = OperatorFixtureAssetStore(
    [
        StaffAssetRow(
            asset_id="council-2026-05-08",
            title="Council - May 8, 2026",
            state="validated",
            manifest_url="https://cdn.example/council-2026-05-08/playlist.m3u8",
            published_at=datetime(2026, 5, 8, 20, 15, tzinfo=UTC),
            retention_policy="meeting",
            version=1,
        ),
        StaffAssetRow(
            asset_id="training-clip",
            title="Training clip",
            state="validated",
            manifest_url=None,
            retention_policy="short",
            version=1,
        ),
        StaffAssetRow(
            asset_id="concert-archive",
            title="Concert archive",
            state="validated",
            manifest_url="https://cdn.example/concert/playlist.m3u8",
            retention_policy="short",
            version=1,
        ),
    ]
)
publish_store = InMemoryPublishStore()
app = create_app()
app.dependency_overrides[get_postgres_store] = lambda: asset_store
app.dependency_overrides[get_publish_store] = lambda: publish_store
