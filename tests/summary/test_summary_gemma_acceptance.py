# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Gemma 4 summaries must not be thrown out for reasons unrelated to their evidence.

Field evidence (2026-11-09, PHI4-MINI-TEST.md v1.6, seven Longmont meetings):
the shipped request cut every transcript to Ollama's 4,096-token default and
let Gemma 4 spend its whole budget "thinking" (empty answer); with those fixed,
gemma4:12b's accurate summaries were still refused because it numbers claims
1, 2, 3 and because one citation ran past its caption line, and gemma4:e4b's
because the prompt never said motions and dollar amounts are quantitative.
"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from civiccast.captions import CaptionCue
from civiccast.summary.generate import DeterministicSummaryModel, SummaryGenerationPipeline


def _cue(cue_id: str, start: float, end: float, text: str) -> CaptionCue:
    return CaptionCue(
        cue_id=cue_id,
        start_seconds=start,
        end_seconds=end,
        text=text,
        confidence=0.94,
        low_confidence=False,
    )


_CUES = [
    _cue("cue-000001", 0.0, 4.0, "The board welcomed residents to the meeting."),
    _cue("cue-000002", 4.0, 9.0, "Commissioner Marcy moved to approve the resolution."),
    _cue("cue-000003", 9.0, 12.0, "Seconded by Commissioner McCoy."),
]


def _claim(claim_id: Any, cue_id: str, start: float, end: float, kind: str = "narrative") -> dict:
    return {
        "claim_id": claim_id,
        "text": "claim text",
        "claim_type": kind,
        "transcript_ranges": [{"cue_id": cue_id, "start_seconds": start, "end_seconds": end}],
    }


def _run(output: dict) -> Any:
    # The same answer on the first try and the retry: the fixture model would
    # otherwise fall back to its own built-in valid answer on the retry.
    return SummaryGenerationPipeline(
        model=DeterministicSummaryModel(outputs=[output, output])
    ).generate(meeting_id="meeting-1", cues=_CUES)


class TestClaimNormalisation:
    def test_numeric_claim_ids_are_accepted_as_their_string_form(self) -> None:
        draft = _run(
            {
                "narrative": "The board approved the resolution.",
                "sourced_claims": [
                    _claim(1, "cue-000001", 0.0, 4.0),
                    _claim(2, "cue-000002", 4.0, 9.0, "quantitative"),
                ],
            }
        )

        assert draft.status == "pending_review"
        assert [c.claim_id for c in draft.sourced_claims] == ["1", "2"]

    def test_a_citation_that_runs_past_its_caption_line_is_clipped_to_that_line(self) -> None:
        # The model cited cue-000002 but gave a range spanning into the next line,
        # and a rounding slip just before the line's start. The citation still
        # names a real line it overlaps, so the evidence becomes that line.
        draft = _run(
            {
                "narrative": "The board approved the resolution.",
                "sourced_claims": [
                    _claim("c1", "cue-000002", 3.98, 11.5, "quantitative"),
                ],
            }
        )

        assert draft.status == "pending_review"
        (rng,) = draft.sourced_claims[0].transcript_ranges
        assert (rng.cue_id, rng.start_seconds, rng.end_seconds) == ("cue-000002", 4.0, 9.0)

    def test_one_misplaced_claim_is_dropped_and_reported_not_the_whole_summary(self) -> None:
        draft = _run(
            {
                "narrative": "The board approved the resolution.",
                "sourced_claims": [
                    _claim("c1", "cue-000002", 4.0, 9.0, "quantitative"),
                    # Names a real line but a time ten minutes away from it.
                    _claim("c2", "cue-000001", 600.0, 604.0),
                    # Names a line that does not exist.
                    _claim("c3", "cue-999999", 1.0, 2.0),
                ],
            }
        )

        assert draft.status == "pending_review"
        assert [c.claim_id for c in draft.sourced_claims] == ["c1"]
        assert draft.operator_message is not None
        assert "2 claims were removed" in draft.operator_message

    def test_still_refused_when_no_claim_survives(self) -> None:
        bad = {
            "narrative": "The board approved the resolution.",
            "sourced_claims": [_claim("c1", "cue-999999", 1.0, 2.0, "quantitative")],
        }

        assert _run(bad).status == "refused"

    def test_still_refused_when_motion_evidence_has_no_quantitative_claim(self) -> None:
        narrative_only = {
            "narrative": "The board met.",
            "sourced_claims": [_claim("c1", "cue-000001", 0.0, 4.0)],
        }

        assert _run(narrative_only).status == "refused"

    def test_an_empty_narrative_is_refused_not_sent_for_review(self) -> None:
        # The parser turns an unreadable answer into an empty summary; with no
        # quantitative facts in the transcript that used to pass as pending_review.
        cues = [_cue("cue-000001", 0.0, 4.0, "The board welcomed residents.")]
        empty = {"narrative": "", "sourced_claims": []}
        draft = SummaryGenerationPipeline(
            model=DeterministicSummaryModel(outputs=[empty, empty])
        ).generate(meeting_id="meeting-1", cues=cues)

        assert draft.status == "refused"

    @pytest.mark.parametrize(
        "written",
        [
            "cue-000002 [4.00-9.00]",  # the transcript line's own format, copied
            "[4.00-9.00]",  # times only (gemma4:e4b)
            "[4.00-9.00] from cue-000002",
            "cue-000002",  # line ID only: the whole line
        ],
    )
    def test_a_citation_written_as_text_is_read_and_located(self, written: str) -> None:
        draft = _run(
            {
                "narrative": "The board approved the resolution.",
                "sourced_claims": [
                    {
                        "claim_id": "claim-1",
                        "text": "Commissioner Marcy moved to approve.",
                        "claim_type": "quantitative",
                        "transcript_ranges": [written],
                    }
                ],
            }
        )

        assert draft.status == "pending_review"
        (rng,) = draft.sourced_claims[0].transcript_ranges
        assert (rng.cue_id, rng.start_seconds, rng.end_seconds) == ("cue-000002", 4.0, 9.0)

    def test_exact_times_of_the_next_line_win_over_an_off_by_one_label(self) -> None:
        # Gemma 4 often copies a line's times correctly but labels them with the
        # line before (it cites cue-000001 with cue-000002's exact times).
        draft = _run(
            {
                "narrative": "The board approved the resolution.",
                "sourced_claims": [_claim("c1", "cue-000001", 4.0, 9.0, "quantitative")],
            }
        )

        assert draft.status == "pending_review"
        (rng,) = draft.sourced_claims[0].transcript_ranges
        assert rng.cue_id == "cue-000002"

    def test_text_citation_with_times_matching_no_line_is_removed(self) -> None:
        draft = _run(
            {
                "narrative": "The board approved the resolution.",
                "sourced_claims": [
                    _claim("c1", "cue-000002", 4.0, 9.0, "quantitative"),
                    {
                        "claim_id": "c2",
                        "text": "x",
                        "claim_type": "narrative",
                        "transcript_ranges": ["[500.00-510.00]"],
                    },
                ],
            }
        )

        assert [c.claim_id for c in draft.sourced_claims] == ["c1"]


class TestPrompt:
    def test_rules_are_repeated_after_the_transcript_and_define_quantitative(self) -> None:
        summary_module = import_module("civiccast.summary.ollama")
        prompt = summary_module._summary_prompt(
            meeting_id="m1", cues=_CUES, prompt_version="v", evidence_line="e"
        )

        tail = prompt[prompt.index("cue-000003") :]
        assert "Return only JSON" in tail
        assert "quantitative" in tail and "motion" in tail and "dollar" in tail
        assert '{"cue_id": "cue-000012", "start_seconds": 61.2, "end_seconds": 66.4}' in tail
        assert "exact start_seconds and end_seconds" in tail


def _adapter(summary_module: Any) -> Any:
    return summary_module.OllamaSummaryModel(
        model_tag="gemma4:12b",
        provenance=summary_module.OllamaSummaryProvenance(
            digest="sha256:" + ("a" * 64),
            ollama_version="ollama test",
            manifest_source="test",
            evidence_line="runtime=ollama",
        ),
    )


@pytest.fixture
def summary_module(monkeypatch: MonkeyPatch) -> Any:
    module = import_module("civiccast.summary.ollama")
    monkeypatch.setattr(
        module,
        "psutil",
        SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=64 * 1024**3)),
        raising=False,
    )
    return module


class TestOllamaRequest:
    def test_thinking_is_off_and_the_context_fits_the_transcript(
        self, summary_module: Any, monkeypatch: MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def fake_generate(**kwargs: Any) -> str:
            seen.update(kwargs)
            return '{"narrative":"ok","sourced_claims":[]}'

        monkeypatch.setattr(summary_module, "generate_with_ollama", fake_generate)
        cues = [_cue(f"cue-{i:06d}", i * 4.0, i * 4.0 + 4.0, "word " * 30) for i in range(400)]

        _adapter(summary_module).generate(meeting_id="m1", cues=cues, prompt_version="v")

        prompt_chars = len(seen["prompt"])
        assert seen["think"] is False
        num_ctx = seen["options"]["num_ctx"]
        # Room for the whole prompt (Gemma ~2 chars/token) plus the answer.
        assert num_ctx >= prompt_chars // 2 + 2048
        assert num_ctx > 4096
        assert num_ctx % 1024 == 0

    def test_a_transcript_too_long_for_one_pass_is_refused_before_inference(
        self, summary_module: Any, monkeypatch: MonkeyPatch
    ) -> None:
        called = False

        def fake_generate(**_kwargs: Any) -> str:
            nonlocal called
            called = True
            return "{}"

        monkeypatch.setattr(summary_module, "generate_with_ollama", fake_generate)
        cues = [_cue(f"cue-{i:06d}", i * 4.0, i * 4.0 + 4.0, "word " * 30) for i in range(1200)]

        with pytest.raises(summary_module.SummaryTranscriptTooLongError, match="too long"):
            _adapter(summary_module).generate(meeting_id="m1", cues=cues, prompt_version="v")
        assert called is False


class TestOllamaClientPayload:
    def _capture(self, monkeypatch: MonkeyPatch) -> dict[str, Any]:
        client = import_module("civiccast.ai_runtime.ollama_client")
        seen: dict[str, Any] = {}

        def fake_request(method: str, url: str, payload: Any = None, **_kw: Any) -> dict:
            seen["payload"] = payload
            return {"response": "ok"}

        monkeypatch.setattr(client, "_request_json", fake_request)
        seen["client"] = client
        return seen

    def test_think_and_options_are_sent_when_given(self, monkeypatch: MonkeyPatch) -> None:
        seen = self._capture(monkeypatch)
        seen["client"].generate_with_ollama(
            model="gemma4:12b", prompt="p", think=False, options={"num_ctx": 8192}
        )

        payload = seen["payload"]
        assert payload["think"] is False
        assert payload["options"] == {"temperature": 0, "num_ctx": 8192}

    def test_default_request_is_unchanged_for_other_callers(self, monkeypatch: MonkeyPatch) -> None:
        seen = self._capture(monkeypatch)
        seen["client"].generate_with_ollama(model="translategemma:4b", prompt="p")

        assert "think" not in seen["payload"]
        assert seen["payload"]["options"] == {"temperature": 0}
