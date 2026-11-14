# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Ollama summary adapter and validation seam for v1.1 release proof."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import psutil

from civiccast.ai_models.catalog import catalog_tiers_for
from civiccast.ai_models.models import ModelTier
from civiccast.ai_runtime.evidence import RuntimeEvidence
from civiccast.ai_runtime.ollama_client import (
    DEFAULT_OLLAMA_BASE_URL,
    OllamaRuntimeUnavailableError,
    generate_with_ollama,
    get_ollama_model_manifest,
)
from civiccast.captions import CaptionCue

_SUMMARY_MODEL_TAG = "gemma4:e4b"
# A tier's catalog ``min_ram_gb`` is a machine spec: the TOTAL RAM a station needs
# to run that tier (the same number the operator console shows as "needs N GB" and
# the adaptive default reads). The run-time check is separate: the tier's floor must
# also be AVAILABLE right now, plus this reserve. ``available`` already excludes the
# memory the running broadcast is using, so the reserve only has to cover growth
# (another channel starting), not the whole broadcast. The old rule demanded the
# floor plus 8 GiB available, which counted the machine spec as free memory and
# meant e4b (8 GB) needed 16 GiB free: a 16 GB station could never summarise.
# Field run 2026-11-13 (rc.6, MSI, 15.7 GB total, RTX 4050 6 GB): 9.9 GiB available
# while broadcasting 3 channels; with the gate off, e4b summarised in 66 s and the
# station stayed healthy through the soak.
_BROADCAST_RESERVE_GB = 1
_GIB = 1024**3

# Context sizing. Without an explicit num_ctx Ollama uses 4,096 tokens and drops
# the start of a longer prompt -- which is where the instructions were, so every
# meeting over ~10 minutes reached the model as its last few minutes with no
# instructions (field run 2026-11-09). Gemma 4 measured 2.05-2.68 characters per
# token on caption transcripts, so 2 characters per token never under-sizes it.
_CHARS_PER_TOKEN_ESTIMATE = 2
_ANSWER_TOKENS = 4096
_MIN_CONTEXT_TOKENS = 4096
# One pass covers a meeting of roughly 80 minutes. Longer meetings are refused
# with a clear reason instead of being summarised from a silently cut transcript.
_MAX_CONTEXT_TOKENS = 49152
_CONTEXT_STEP_TOKENS = 1024


class SummaryResourceDeferredError(OllamaRuntimeUnavailableError):
    """Raised before inference when the selected tier would consume broadcast RAM."""


class SummaryTranscriptTooLongError(SummaryResourceDeferredError):
    """Raised before inference when the transcript cannot fit one summary pass."""


def _summary_context_tokens(prompt: str) -> int:
    """Context window that holds the whole prompt plus the answer, or refuse."""

    needed = len(prompt) // _CHARS_PER_TOKEN_ESTIMATE + _ANSWER_TOKENS
    if needed > _MAX_CONTEXT_TOKENS:
        raise SummaryTranscriptTooLongError(
            "This meeting's transcript is too long for one summary pass "
            f"(about {needed:,} tokens; the limit is {_MAX_CONTEXT_TOKENS:,}, "
            "roughly 80 minutes of meeting). Summaries of longer meetings are not "
            "supported yet; write this summary by hand."
        )
    steps = -(-needed // _CONTEXT_STEP_TOKENS)
    return max(_MIN_CONTEXT_TOKENS, steps * _CONTEXT_STEP_TOKENS)


def _local_tiers() -> list[ModelTier]:
    return [
        tier
        for tier in catalog_tiers_for("summary")
        if tier.provider == "ollama" and not tier.requires_network
    ]


def _smaller_local_tier(floor_gb: int) -> ModelTier | None:
    """The first on-box summary tier with a lower RAM floor, or ``None``."""

    return next((tier for tier in _local_tiers() if tier.min_ram_gb < floor_gb), None)


def _require_summary_headroom(model_tag: str) -> None:
    """Defer before loading a tier this station cannot run without hurting broadcast.

    Two checks, in order:

    1. Machine spec: total physical RAM (whole GiB, truncated -- the same rule as
       ``detect_summary_model_default`` and the installer self-test) must meet the
       tier's catalog ``min_ram_gb``.
    2. Right now: available RAM must cover that floor plus ``_BROADCAST_RESERVE_GB``.
    """

    tier = next(
        (
            candidate
            for candidate in catalog_tiers_for("summary")
            if candidate.model_id == model_tag
        ),
        None,
    )
    model_floor_gb = tier.min_ram_gb if tier is not None else 8
    smaller_tier = _smaller_local_tier(model_floor_gb)
    memory = psutil.virtual_memory()

    total_gb = int(memory.total / _GIB)
    if total_gb < model_floor_gb:
        recovery_action = "Select a tier this computer can run, or a cloud tier."
        if smaller_tier is not None:
            recovery_action = f"Select the smaller model ({smaller_tier.model_id})."
        raise SummaryResourceDeferredError(
            f"Summary generation was deferred before loading {model_tag}: "
            f"this computer has {memory.total / _GIB:.1f} GiB of RAM, while this tier "
            f"needs a {model_floor_gb} GB station. " + recovery_action
        )

    required_available_gb = model_floor_gb + _BROADCAST_RESERVE_GB
    if memory.available < required_available_gb * _GIB:
        recovery_action = "Wait for station memory to recover before retrying."
        if smaller_tier is not None:
            recovery_action = (
                "Wait for station memory to recover or select the smaller model "
                f"({smaller_tier.model_id})."
            )
        raise SummaryResourceDeferredError(
            f"Summary generation was deferred before loading {model_tag}: "
            f"{memory.available / _GIB:.1f} GiB is available, while this tier needs "
            f"{model_floor_gb} GiB free plus {_BROADCAST_RESERVE_GB} GiB kept for broadcast. "
            + recovery_action
        )


@dataclass(frozen=True)
class OllamaSummaryProvenance:
    """Live Ollama model provenance captured before summary generation."""

    digest: str
    ollama_version: str
    manifest_source: str
    evidence_line: str


@dataclass(frozen=True)
class OllamaSummaryModel:
    """Release summary adapter descriptor."""

    model_tag: str
    provenance: OllamaSummaryProvenance
    base_url: str = DEFAULT_OLLAMA_BASE_URL

    @classmethod
    def for_release(
        cls,
        *,
        model_tag: str = _SUMMARY_MODEL_TAG,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
    ) -> OllamaSummaryModel:
        manifest = get_ollama_model_manifest(model_tag, base_url=base_url)
        evidence = RuntimeEvidence(
            runtime="ollama",
            model=manifest.model,
            compute=None,
            digest=manifest.digest,
            runtime_version=manifest.runtime_version,
            manifest_source=manifest.manifest_source,
        )
        return cls(
            model_tag=manifest.model,
            provenance=OllamaSummaryProvenance(
                digest=manifest.digest,
                ollama_version=manifest.runtime_version,
                manifest_source=manifest.manifest_source,
                evidence_line=evidence.to_machine_line(),
            ),
            base_url=base_url,
        )

    def smaller_fallback(self) -> OllamaSummaryModel | None:
        """The next smaller on-box summary tier, or ``None`` when this is the smallest.

        Used when this tier is deferred for memory: the adaptive default picks
        gemma4:12b on a GPU box with 16 GB+ RAM, but when less than its 16 GiB floor
        (plus the broadcast reserve) is free, e4b's 8 GiB often still is.
        """

        tiers = catalog_tiers_for("summary")
        current = next((tier for tier in tiers if tier.model_id == self.model_tag), None)
        floor_gb = current.min_ram_gb if current is not None else 8
        smaller = _smaller_local_tier(floor_gb)
        if smaller is None:
            return None
        return OllamaSummaryModel.for_release(model_tag=smaller.model_id, base_url=self.base_url)

    def generate(
        self,
        *,
        meeting_id: str,
        cues: list[CaptionCue],
        prompt_version: str,
    ) -> dict[str, Any]:
        """Generate structured summary JSON with the live local Ollama model."""

        _require_summary_headroom(self.model_tag)
        prompt = _summary_prompt(
            meeting_id=meeting_id,
            cues=cues,
            prompt_version=prompt_version,
            evidence_line=self.provenance.evidence_line,
        )
        num_ctx = _summary_context_tokens(prompt)
        response = generate_with_ollama(
            model=self.model_tag,
            prompt=prompt,
            base_url=self.base_url,
            # Gemma 4 otherwise reasons first and, on a real meeting, can use the
            # whole budget doing so and return an empty answer (12b: 28,444
            # tokens of reasoning, nothing returned; with think off, 24 s).
            think=False,
            options={"num_ctx": num_ctx},
        )
        return _parse_model_json(response)


@dataclass(frozen=True)
class SummaryValidationResult:
    """Closed validation result for model output."""

    status: Literal["ok", "refused"]
    operator_action: str


def validate_ollama_summary_output(
    *,
    transcript_text: str,
    model_output: str,
    citations: list[str],
) -> SummaryValidationResult:
    """Refuse summary text containing claims unsupported by transcript citations."""

    supported_text = "\n".join([transcript_text, *citations]).casefold()
    unsupported = [
        sentence
        for sentence in _sentences(model_output)
        if sentence.casefold() not in supported_text
    ]
    if unsupported:
        return SummaryValidationResult(
            status="refused",
            operator_action=(
                "Ollama summary refused because an unsupported claim was not tied "
                "to transcript evidence. Remove or cite: " + unsupported[0]
            ),
        )
    return SummaryValidationResult(
        status="ok",
        operator_action="Summary output is tied to supplied transcript citations.",
    )


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in text.replace("?", ".").replace("!", ".").split(".")]
    return [part + "." for part in parts if part]


def _summary_prompt(
    *,
    meeting_id: str,
    cues: list[CaptionCue],
    prompt_version: str,
    evidence_line: str,
) -> str:
    transcript = "\n".join(
        f"- {cue.cue_id} [{cue.start_seconds:.2f}-{cue.end_seconds:.2f}]: {cue.text}"
        for cue in cues
    )
    return (
        "You are CivicCast's local summary model. You will read a meeting "
        "transcript and summarise it. The rules follow the transcript.\n\n"
        f"meeting_id: {meeting_id}\n"
        f"prompt_version: {prompt_version}\n"
        f"runtime_evidence: {evidence_line}\n"
        f"transcript:\n{transcript}\n\n"
        # The rules come after the transcript so that, if anything is ever cut,
        # the instructions survive (Ollama keeps the end of a prompt).
        f"{_SUMMARY_RULES}\n"
    )


_SUMMARY_RULES = (
    "Rules. Return only JSON with keys `narrative` and `sourced_claims`. "
    "`narrative` is a short plain-English summary of what the meeting discussed "
    "and decided. Every sourced_claim must include `claim_id` (a string such as "
    '"claim-1"), `text`, `claim_type`, and `transcript_ranges`. The only allowed '
    "claim_type values are `narrative` and `quantitative`. Use `quantitative` for "
    "every claim about a motion, a second, a vote or its result, or a dollar "
    "amount, and include at least one such claim when the transcript has any. "
    "Every transcript range must cite one cue_id from the transcript and copy "
    "that line's exact start_seconds and end_seconds as shown in its brackets, "
    "written as an object, for example "
    '{"cue_id": "cue-000012", "start_seconds": 61.2, "end_seconds": 66.4}. '
    "Refuse by returning an empty narrative and empty sourced_claims if the "
    "transcript does not support a claim."
)


def _parse_model_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return {"narrative": "", "sourced_claims": []}
        try:
            parsed = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return {"narrative": "", "sourced_claims": []}
    if not isinstance(parsed, dict):
        return {"narrative": "", "sourced_claims": []}
    return parsed
