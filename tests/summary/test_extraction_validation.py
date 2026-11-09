# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Contract tests for v0.6 quantitative extraction and sourced-claim validation."""

from __future__ import annotations

from typing import ClassVar

import pytest

from civiccast.captions import CaptionCue
from civiccast.summary.extract import extract_quantitative_facts
from civiccast.summary.generate import DeterministicSummaryModel, SummaryGenerationPipeline
from civiccast.summary.models import SourcedClaim, TranscriptRange
from civiccast.summary.validate import SourcedClaimValidator, UnsupportedSummaryClaimError


def _cue(
    cue_id: str,
    start: float,
    end: float,
    text: str,
    confidence: float = 0.94,
) -> CaptionCue:
    return CaptionCue(
        cue_id=cue_id,
        start_seconds=start,
        end_seconds=end,
        text=text,
        confidence=confidence,
        low_confidence=confidence < 0.8,
    )


class TestQuantitativeExtraction:
    def test_extracts_motion_second_vote_tally_and_dollar_amount_from_committed_cues(self) -> None:
        cues = [
            _cue("cue-1", 10.0, 14.0, "Councilmember Rivera moved to approve $12,500."),
            _cue("cue-2", 14.0, 18.0, "Councilmember Chen seconded the motion."),
            _cue(
                "cue-3", 18.0, 24.0, "Roll call: Rivera yes, Chen yes, Malik no. Motion passes 2-1."
            ),
        ]

        facts = extract_quantitative_facts(cues)

        assert {fact.kind for fact in facts} >= {"motion", "second", "vote_tally", "dollar_amount"}
        assert {fact.source_range.cue_id for fact in facts} == {"cue-1", "cue-2", "cue-3"}
        assert all(
            fact.source_range.start_seconds < fact.source_range.end_seconds for fact in facts
        )


class TestSourcedClaimValidation:
    def test_rejects_quantitative_claim_without_committed_cue_timestamp_support(self) -> None:
        validator = SourcedClaimValidator(
            [_cue("cue-1", 0.0, 5.0, "The board discussed the budget.")]
        )
        claim = SourcedClaim(
            claim_id="claim-1",
            text="The motion passed 4-1.",
            claim_type="quantitative",
            transcript_ranges=[
                TranscriptRange(cue_id="cue-404", start_seconds=12.0, end_seconds=15.0)
            ],
        )

        with pytest.raises(UnsupportedSummaryClaimError, match="committed transcript cue"):
            validator.validate_claim(claim)

    def test_preserves_non_quantitative_source_metadata(self) -> None:
        validator = SourcedClaimValidator(
            [_cue("cue-1", 0.0, 5.0, "The board discussed the budget.")]
        )
        claim = SourcedClaim(
            claim_id="claim-2",
            text="The board discussed the budget.",
            claim_type="narrative",
            transcript_ranges=[TranscriptRange(cue_id="cue-1", start_seconds=0.0, end_seconds=5.0)],
        )

        assert validator.validate_claim(claim).transcript_ranges[0].cue_id == "cue-1"


class TestSummaryGenerationRetry:
    def test_invalid_first_output_retries_once_with_stronger_prompt(self) -> None:
        model = DeterministicSummaryModel(
            outputs=[
                {"narrative": "The motion passed 4-1.", "sourced_claims": []},
                {
                    "narrative": "The motion passed 2-1.",
                    "sourced_claims": [
                        {
                            "claim_id": "claim-1",
                            "text": "The motion passed 2-1.",
                            "claim_type": "quantitative",
                            "transcript_ranges": [
                                {"cue_id": "cue-1", "start_seconds": 18.0, "end_seconds": 24.0}
                            ],
                        }
                    ],
                },
            ]
        )
        pipeline = SummaryGenerationPipeline(model=model)

        result = pipeline.generate(
            meeting_id="meeting-1",
            cues=[_cue("cue-1", 18.0, 24.0, "Motion passes 2-1.")],
        )

        assert result.status == "pending_review"
        assert model.prompt_versions == ["summary-v0.7", "summary-v0.7-retry-sourced-claims"]

    def test_second_invalid_output_surfaces_refusal_to_operator(self) -> None:
        model = DeterministicSummaryModel(outputs=[{}, {}])
        pipeline = SummaryGenerationPipeline(model=model)

        result = pipeline.generate(
            meeting_id="meeting-1",
            cues=[_cue("cue-1", 18.0, 24.0, "Motion passes 2-1.")],
        )

        assert result.status == "refused"
        assert result.operator_message
        assert "timestamp evidence" in result.operator_message.lower()
        assert len(model.prompt_versions) == 2


class _FakeSummaryProvenance:
    digest = "sha256:deadbeef"
    ollama_version = "0.5.7"


class _FakeRealSummaryModel:
    """Stand-in for OllamaSummaryModel: exposes real model_tag + provenance."""

    model_tag = "gemma4:e4b"
    provenance: ClassVar[_FakeSummaryProvenance] = _FakeSummaryProvenance()

    def __init__(self, output: dict) -> None:
        self._output = output
        self.prompt_versions: list[str] = []

    def generate(self, *, meeting_id: str, cues, prompt_version: str) -> dict:
        self.prompt_versions.append(prompt_version)
        return self._output


class TestSummaryProvenanceStamping:
    """A real, records-bound draft must carry the real model's provenance —
    not the fixture tag that the pipeline used to hardcode over it."""

    _OUTPUT: ClassVar[dict] = {
        "narrative": "The board welcomed residents to the meeting.",
        "sourced_claims": [
            {
                "claim_id": "claim-1",
                "text": "The board welcomed residents to the meeting.",
                "claim_type": "narrative",
                "transcript_ranges": [
                    {"cue_id": "cue-1", "start_seconds": 0.0, "end_seconds": 5.0}
                ],
            }
        ],
    }

    def test_real_model_provenance_is_recorded_not_a_fixture_tag(self) -> None:
        model = _FakeRealSummaryModel(self._OUTPUT)
        pipeline = SummaryGenerationPipeline(model=model)

        result = pipeline.generate(
            meeting_id="meeting-1",
            cues=[_cue("cue-1", 0.0, 5.0, "The board welcomed residents to the meeting.")],
        )

        assert result.status == "pending_review"
        assert result.provenance.model_tag == "gemma4:e4b"
        assert result.provenance.model_digest == "sha256:deadbeef"
        assert result.provenance.ollama_version == "0.5.7"

    def test_test_fixture_model_still_stamps_the_fixture_tag(self) -> None:
        # A genuine test fixture (no model_tag) honestly falls back to the
        # fixture label rather than claiming a real model ran.
        model = DeterministicSummaryModel()
        pipeline = SummaryGenerationPipeline(model=model)

        result = pipeline.generate(
            meeting_id="meeting-1",
            cues=[_cue("cue-1", 0.0, 5.0, "The board welcomed residents to the meeting.")],
        )

        assert result.provenance.model_tag == "fixture-summary-no-real-model"
        assert result.provenance.model_digest is None
