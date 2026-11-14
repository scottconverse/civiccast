# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""The summary memory gate, and the 12b -> e4b fallback when 12b is deferred.

Field evidence (2026-11-13, rc.6):
- RTX 5060 Ti station, 31.3 GB RAM, 21.5 GiB available: the old guard needed
  16 + 8 GiB available for 12b, so every default summary was deferred.
- MSI station, 15.7 GB RAM, RTX 4050 6 GB, 9.9 GiB available while broadcasting
  3 channels: the old guard needed 8 + 8 GiB for e4b, so a 16 GB station could
  never summarise. With the guard off, e4b summarised in 66 s (6 claims) and the
  station stayed healthy.

The rule now: total RAM (whole GiB) >= the tier's catalog floor, and available
RAM >= that floor + 1 GiB broadcast reserve.
"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from civiccast.captions import CaptionCue
from civiccast.summary.generate import SummaryGenerationPipeline

_CUES = [
    CaptionCue(
        cue_id="cue-000001",
        start_seconds=0.0,
        end_seconds=6.0,
        text="The council opened the meeting and took roll.",
        confidence=0.94,
        low_confidence=False,
    )
]
_ANSWER = (
    '{"narrative":"The council opened the meeting.","sourced_claims":[{"claim_id":"c1",'
    '"text":"The council opened the meeting.","claim_type":"narrative","transcript_ranges":'
    '[{"cue_id":"cue-000001","start_seconds":0.0,"end_seconds":6.0}]}]}'
)


@pytest.fixture
def ollama(monkeypatch: MonkeyPatch) -> Any:
    module = import_module("civiccast.summary.ollama")
    calls: list[str] = []

    def fake_manifest(model_tag: str, **_kw: Any) -> Any:
        return SimpleNamespace(
            model=model_tag,
            digest="sha256:" + ("e" if "e4b" in model_tag else "c") * 64,
            runtime_version="0.40.13",
            manifest_source="test",
        )

    def fake_generate(**kwargs: Any) -> str:
        calls.append(kwargs["model"])
        return _ANSWER

    monkeypatch.setattr(module, "get_ollama_model_manifest", fake_manifest)
    monkeypatch.setattr(module, "generate_with_ollama", fake_generate)
    monkeypatch.setattr(module, "calls", calls, raising=False)
    return module


def _available(monkeypatch: MonkeyPatch, module: Any, gib: float, *, total: float = 31.3) -> None:
    memory = SimpleNamespace(total=int(total * 1024**3), available=int(gib * 1024**3))
    monkeypatch.setattr(module, "psutil", SimpleNamespace(virtual_memory=lambda: memory))


def test_msi_16gb_station_runs_e4b(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 9.9, total=15.7)  # MSI rc.6 reading, 3 channels on air
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:e4b")

    draft = SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)

    assert ollama.calls == ["gemma4:e4b"]
    assert draft.provenance.model_tag == "gemma4:e4b"
    assert draft.operator_message is None


def test_msi_16gb_station_12b_falls_back_to_e4b_on_total_ram(
    ollama: Any, monkeypatch: MonkeyPatch
) -> None:
    # 15.7 GiB truncates to 15 < 16: 12b is below this machine's spec, e4b is not.
    _available(monkeypatch, ollama, 9.9, total=15.7)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:12b")

    draft = SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)

    assert set(ollama.calls) == {"gemma4:e4b"}
    assert draft.provenance.model_tag == "gemma4:e4b"


def test_total_ram_below_floor_defers_with_select_smaller(
    ollama: Any, monkeypatch: MonkeyPatch
) -> None:
    _available(monkeypatch, ollama, 14.0, total=15.7)

    with pytest.raises(ollama.SummaryResourceDeferredError) as excinfo:
        ollama._require_summary_headroom("gemma4:12b")
    message = str(excinfo.value)
    assert "15.7 GiB of RAM" in message and "16 GB station" in message
    assert "Select the smaller model (gemma4:e4b)" in message


def test_total_ram_below_e4b_floor_defers(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 6.0, total=7.8)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:e4b")

    with pytest.raises(ollama.SummaryResourceDeferredError, match="8 GB station"):
        SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)
    assert ollama.calls == []


def test_low_available_defers_e4b(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 3.0, total=15.7)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:e4b")

    with pytest.raises(ollama.SummaryResourceDeferredError, match=r"3.0 GiB is available"):
        SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)
    assert ollama.calls == []


def test_e4b_needs_its_floor_plus_reserve_available(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 8.9, total=15.7)
    with pytest.raises(ollama.SummaryResourceDeferredError):
        ollama._require_summary_headroom("gemma4:e4b")

    _available(monkeypatch, ollama, 9.0, total=15.7)
    ollama._require_summary_headroom("gemma4:e4b")


def test_5060ti_reading_now_keeps_12b(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    # 31.3 GB total, 21.5 GiB available: 12b needs 16 + 1, so it is no longer deferred.
    _available(monkeypatch, ollama, 21.5, total=31.3)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:12b")

    draft = SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)

    assert set(ollama.calls) == {"gemma4:12b"}
    assert draft.operator_message is None


def test_12b_short_of_memory_runs_e4b_and_records_e4b(
    ollama: Any, monkeypatch: MonkeyPatch
) -> None:
    # 32 GB station with load on it: 12b needs 16 + 1 GiB free, e4b needs 8 + 1.
    _available(monkeypatch, ollama, 12.0, total=31.3)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:12b")

    draft = SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)

    assert ollama.calls and set(ollama.calls) == {"gemma4:e4b"}
    assert draft.status == "pending_review"
    assert draft.provenance.model_tag == "gemma4:e4b"
    assert draft.provenance.model_digest == "sha256:" + "e" * 64
    assert draft.operator_message is not None
    assert "gemma4:e4b" in draft.operator_message and "gemma4:12b" in draft.operator_message


def test_enough_memory_keeps_12b(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 30.0)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:12b")

    draft = SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)

    assert set(ollama.calls) == {"gemma4:12b"}
    assert draft.provenance.model_tag == "gemma4:12b"
    assert draft.operator_message is None


def test_too_little_memory_for_e4b_is_still_deferred(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 5.0)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:12b")

    with pytest.raises(ollama.SummaryResourceDeferredError, match="gemma4:e4b"):
        SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)
    assert ollama.calls == []


def test_e4b_short_of_memory_has_no_smaller_tier(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 5.0)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:e4b")

    assert model.smaller_fallback() is None
    with pytest.raises(ollama.SummaryResourceDeferredError):
        SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)
