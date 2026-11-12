# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""One-time repair of a saved channel codec the selected engine cannot put on air.

Issue #327 (follow-up to #324). Up to v1.0.0-rc.5 a cable preset saved its codec
on the CHANNEL (``canonical_profile.audio_codec = "ac3"``, and ``mpeg2video`` for
``comcast-mtd-sd``). On the GStreamer engine such a channel never goes on air.
Since #326 a preset keeps the channel on AAC / H.264 and puts AC-3 on the cable
output alone (``-c:a ac3``), and ``PUT .../config`` refuses the old shape -- but a
station that saved the old shape BEFORE its upgrade would stay dark until an
operator applied the preset again. This moves the saved config to the new shape
at service start, so an upgrade alone brings the channel back.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from civiccast.egress.engine_select import gstreamer_engine_selected
from civiccast.egress.gst.bridge import (
    SUPPORTED_AUDIO_CODECS,
    SUPPORTED_SINK_AUDIO_CODECS,
    UNAIRABLE_VIDEO_CODECS,
)
from civiccast.egress.models import SINK_AUDIO_CODEC_FLAG, EgressConfig, EgressSinkSpec

__all__ = ["repair_unairable_channel_codecs", "repaired_config"]

_FALLBACK_AUDIO_CODEC = "aac"
_FALLBACK_VIDEO_CODEC = "h264"
#: The sink kinds a cable preset creates (``civiccast.egress.headend``). Only
#: these inherit the old channel-level codec; web and streaming outputs need AAC.
_CABLE_SINK_KINDS = frozenset({"udp-ts", "file"})


def _with_sink_codec(sink: EgressSinkSpec, codec: str) -> EgressSinkSpec:
    if sink.kind not in _CABLE_SINK_KINDS or SINK_AUDIO_CODEC_FLAG in sink.extra_output_args:
        return sink
    return sink.model_copy(
        update={"extra_output_args": [*sink.extra_output_args, SINK_AUDIO_CODEC_FLAG, codec]}
    )


def repaired_config(config: EgressConfig) -> EgressConfig | None:
    """``config`` in the shape the GStreamer engine airs, or ``None`` if it already is.

    Audio: the channel goes back to AAC; when the engine can encode the old codec
    per output (AC-3), every cable output keeps it through ``-c:a``. Video:
    MPEG-2 is not built on this engine, so the channel goes back to H.264 -- the
    same result a fresh preset apply gives since #326.
    """
    profile = config.canonical_profile
    audio = profile.audio_codec.strip().lower()
    video = profile.video_codec.strip().lower()
    update: dict[str, str] = {}
    sinks = list(config.sinks)
    if audio not in SUPPORTED_AUDIO_CODECS:
        update["audio_codec"] = _FALLBACK_AUDIO_CODEC
        if audio in SUPPORTED_SINK_AUDIO_CODECS:
            sinks = [_with_sink_codec(sink, audio) for sink in sinks]
    if video in UNAIRABLE_VIDEO_CODECS:
        update["video_codec"] = _FALLBACK_VIDEO_CODEC
    if not update:
        return None
    return config.model_copy(
        update={"canonical_profile": profile.model_copy(update=update), "sinks": sinks}
    )


def repair_unairable_channel_codecs(
    list_configs: Callable[[], Iterable[EgressConfig]],
    upsert_config: Callable[[EgressConfig], None],
) -> list[str]:
    """Repair every saved config; return ``"<channel>: <what changed>"`` lines.

    A no-op on the ffmpeg-concat engine, which airs channel-level AC-3 and MPEG-2.
    """
    if not gstreamer_engine_selected():
        return []
    repaired: list[str] = []
    for config in list_configs():
        fixed = repaired_config(config)
        if fixed is None:
            continue
        upsert_config(fixed)
        before, after = config.canonical_profile, fixed.canonical_profile
        changes = [
            f"{kind} {old} -> {new}"
            for kind, old, new in (
                ("audio", before.audio_codec, after.audio_codec),
                ("video", before.video_codec, after.video_codec),
            )
            if old != new
        ]
        cable = [sink.label for sink in fixed.sinks if sink.audio_codec_override]
        if cable and before.audio_codec != after.audio_codec:
            changes.append(f"{before.audio_codec} kept on output(s) {', '.join(cable)}")
        repaired.append(f"{config.channel_id}: {'; '.join(changes)}")
    return repaired
