# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Contracts for v1.1 Ollama summary provenance and validation."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from pytest import MonkeyPatch

from civiccast.ai_runtime.ollama_client import OllamaModelManifest


class TestOllamaSummaryProvenance:
    def test_summary_default_uses_live_gemma4_when_release_mode(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        summary_module = import_module("civiccast.summary.ollama")
        manifest = OllamaModelManifest(
            model="gemma4:e4b",
            digest="sha256:" + ("a" * 64),
            runtime_version="ollama 0.9.0",
            manifest_source="http://127.0.0.1:11434/api/tags#gemma4:e4b",
            details={},
        )

        monkeypatch.setattr(
            summary_module,
            "get_ollama_model_manifest",
            lambda model, *, base_url: manifest,
        )

        adapter = summary_module.OllamaSummaryModel.for_release()

        assert adapter.model_tag == "gemma4:e4b"
        assert adapter.provenance.digest.startswith("sha256:")
        assert adapter.provenance.ollama_version
        assert adapter.provenance.manifest_source
        assert "runtime=ollama" in adapter.provenance.evidence_line

    def test_summary_refuses_unsupported_claims_when_ollama_output_is_uncited(
        self,
    ) -> None:
        summary_module = import_module("civiccast.summary.ollama")

        result = summary_module.validate_ollama_summary_output(
            transcript_text="The council approved item 4A.",
            model_output="The council approved item 4A and sold the city hall.",
            citations=["The council approved item 4A."],
        )

        assert result.status == "refused"
        assert "unsupported claim" in result.operator_action.lower()


def _summary_adapter(summary_module):
    return summary_module.OllamaSummaryModel(
        model_tag="gemma4:12b",
        provenance=summary_module.OllamaSummaryProvenance(
            digest="sha256:" + ("a" * 64),
            ollama_version="ollama test",
            manifest_source="test",
            evidence_line="runtime=ollama",
        ),
    )


def test_summary_defers_before_generate_when_broadcast_headroom_is_short(
    monkeypatch: MonkeyPatch,
) -> None:
    summary_module = import_module("civiccast.summary.ollama")
    called = False

    def unexpected_generate(**_kwargs):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(
        summary_module,
        "psutil",
        SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(total=32 * 1024**3, available=16 * 1024**3)
        ),
        raising=False,
    )
    monkeypatch.setattr(summary_module, "generate_with_ollama", unexpected_generate)

    with pytest.raises(RuntimeError, match="smaller model"):
        _summary_adapter(summary_module).generate(
            meeting_id="meeting-1", cues=[], prompt_version="v1"
        )

    assert called is False


def test_summary_runs_when_model_and_broadcast_headroom_are_available(
    monkeypatch: MonkeyPatch,
) -> None:
    summary_module = import_module("civiccast.summary.ollama")
    monkeypatch.setattr(
        summary_module,
        "psutil",
        SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(total=32 * 1024**3, available=17 * 1024**3)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        summary_module,
        "generate_with_ollama",
        lambda **_kwargs: '{"narrative":"ok","sourced_claims":[]}',
    )

    result = _summary_adapter(summary_module).generate(
        meeting_id="meeting-1", cues=[], prompt_version="v1"
    )

    assert result["narrative"] == "ok"
