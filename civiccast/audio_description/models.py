# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Public job and playback contracts; no local source paths cross this boundary."""

from typing import Literal

from pydantic import BaseModel


class DescriptionCoverage(BaseModel):
    in_gap: int
    requires_pause: int
    uncertain_visuals: int


class AudioDescriptionJob(BaseModel):
    asset_id: str
    job_id: str | None = None
    state: Literal[
        "not_generated", "queued", "running", "complete", "interrupted", "failed", "cancelled"
    ]
    message: str | None = None
    coverage: DescriptionCoverage | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PauseDescription(BaseModel):
    id: int
    time: float
    until: float
    text: str
    audio: str


class DescriptionPlayback(BaseModel):
    coverage: DescriptionCoverage
    limitations: list[str]
    credits: list[str]
    original_url: str
    described_url: str
    captions_url: str
    descriptions_url: str
    pause_descriptions: list[PauseDescription]


class DescriptionPreviewSession(BaseModel):
    url: str
