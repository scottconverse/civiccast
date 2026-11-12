# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Issue #327: a channel codec saved by rc.5 or earlier is repaired at service start."""

from __future__ import annotations

import pytest

from civiccast.egress.codec_repair import repair_unairable_channel_codecs, repaired_config
from civiccast.egress.gst.bridge import output_branches_from_config
from civiccast.egress.headend import apply_headend_profile, get_headend_profile
from civiccast.egress.models import EgressConfig, EgressSinkSpec
from civiccast.egress.store import InMemoryEgressStore


def _rc5_cable_config(profile_id: str = "generic-udp-spts") -> EgressConfig:
    """The shape rc.5 saved: the preset's codec on the CHANNEL, none on the sink."""
    base = EgressConfig(
        channel_id="government",
        enabled=True,
        slate_message="Please stand by",
        sinks=[EgressSinkSpec(kind="hls", label="Web preview (HLS)", uri="C:/hls/government")],
    )
    profile = get_headend_profile(profile_id)
    assert profile is not None
    return apply_headend_profile(
        base, profile, destination_uri="udp://239.1.1.1:5000", keep_existing_sinks=True
    )


def test_rc5_cable_config_is_the_dark_shape() -> None:
    config = _rc5_cable_config()
    assert config.canonical_profile.audio_codec == "ac3"
    assert all(sink.audio_codec_override is None for sink in config.sinks)


def test_repair_moves_ac3_from_the_channel_to_the_cable_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CIVICCAST_EGRESS_ENGINE", raising=False)
    store = InMemoryEgressStore()
    store.upsert_config(_rc5_cable_config())

    lines = repair_unairable_channel_codecs(store.list_configs, store.upsert_config)

    saved = store.get_config("government")
    assert saved is not None
    assert saved.canonical_profile.audio_codec == "aac"
    by_kind = {sink.kind: sink for sink in saved.sinks}
    assert by_kind["udp-ts"].audio_codec_override == "ac3"
    assert by_kind["udp-ts"].extra_output_args[:2] == ["-muxrate", "8000k"]
    assert by_kind["hls"].audio_codec_override is None
    assert len(lines) == 1
    assert lines[0].startswith("government: audio ac3 -> aac")
    assert "Cable headend" in lines[0]
    # The repaired config builds: AC-3 on the cable branch, AAC on the web branch.
    encoders = {
        branch.label: [element.factory for element in branch.audio_chain]
        for branch in output_branches_from_config(saved)
    }
    assert "avenc_ac3" in encoders["Cable headend"]
    assert "avenc_aac" in encoders["Web preview (HLS)"]


def test_repair_runs_once_and_leaves_a_good_config_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CIVICCAST_EGRESS_ENGINE", raising=False)
    store = InMemoryEgressStore()
    store.upsert_config(_rc5_cable_config())
    assert repair_unairable_channel_codecs(store.list_configs, store.upsert_config)
    after_first = store.get_config("government")

    assert repair_unairable_channel_codecs(store.list_configs, store.upsert_config) == []
    assert store.get_config("government") == after_first
    assert after_first is not None
    assert repaired_config(after_first) is None


def test_repair_puts_saved_mpeg2_video_back_to_h264(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CIVICCAST_EGRESS_ENGINE", raising=False)
    store = InMemoryEgressStore()
    store.upsert_config(_rc5_cable_config("comcast-mtd-sd"))

    lines = repair_unairable_channel_codecs(store.list_configs, store.upsert_config)

    saved = store.get_config("government")
    assert saved is not None
    assert saved.canonical_profile.video_codec == "h264"
    assert saved.canonical_profile.audio_codec == "aac"
    assert "video mpeg2video -> h264" in lines[0]


def test_repair_does_nothing_on_the_ffmpeg_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIVICCAST_EGRESS_ENGINE", "ffmpeg-concat")
    store = InMemoryEgressStore()
    before = _rc5_cable_config()
    store.upsert_config(before)

    assert repair_unairable_channel_codecs(store.list_configs, store.upsert_config) == []
    assert store.get_config("government") == before
