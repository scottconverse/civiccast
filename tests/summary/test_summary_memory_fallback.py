# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""A summary deferred for memory on the 12b tier falls back to e4b by itself.

Field evidence (2026-11-13, rc.6 on the RTX 5060 Ti station, 31.3 GB RAM): the
adaptive default picks gemma4:12b on a GPU box with 16 GB+ RAM, but the headroom
guard needs 16 + 8 GiB available, and 21.5 GiB was available. Every default
summary was deferred, and the operator had to change the model by hand.
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


def _available(monkeypatch: MonkeyPatch, module: Any, gib: float) -> None:
    monkeypatch.setattr(
        module,
        "psutil",
        SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=int(gib * 1024**3))),
    )


def test_12b_short_of_memory_runs_e4b_and_records_e4b(
    ollama: Any, monkeypatch: MonkeyPatch
) -> None:
    _available(monkeypatch, ollama, 21.5)  # the 5060 Ti reading: 12b needs 24, e4b needs 16
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
    _available(monkeypatch, ollama, 10.0)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:12b")

    with pytest.raises(ollama.SummaryResourceDeferredError, match="gemma4:e4b"):
        SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)
    assert ollama.calls == []


def test_e4b_short_of_memory_has_no_smaller_tier(ollama: Any, monkeypatch: MonkeyPatch) -> None:
    _available(monkeypatch, ollama, 10.0)
    model = ollama.OllamaSummaryModel.for_release(model_tag="gemma4:e4b")

    assert model.smaller_fallback() is None
    with pytest.raises(ollama.SummaryResourceDeferredError):
        SummaryGenerationPipeline(model=model).generate(meeting_id="m1", cues=_CUES)
