# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""v0.6 summary generation pipeline with deterministic local fixtures."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from civiccast.captions import CaptionCue
from civiccast.summary.extract import extract_quantitative_facts
from civiccast.summary.fingerprint import sha256_fingerprint
from civiccast.summary.minutes import transcript_fingerprint
from civiccast.summary.models import ModelProvenance, SourcedClaim, SummaryDraft
from civiccast.summary.ollama import OllamaSummaryModel
from civiccast.summary.validate import SourcedClaimValidator, UnsupportedSummaryClaimError

# v0.7: rules after the transcript, quantitative defined, string claim ids,
# exact cue times (civiccast/summary/ollama.py::_SUMMARY_RULES).
PROMPT_VERSION = "summary-v0.7"
RETRY_PROMPT_VERSION = "summary-v0.7-retry-sourced-claims"
EXTRACTION_VERSION = "summary-extract-v0.6"


class SummaryModel(Protocol):
    """Protocol for a local or external summary model adapter."""

    def generate(
        self,
        *,
        meeting_id: str,
        cues: list[CaptionCue],
        prompt_version: str,
    ) -> dict[str, Any]:
        """Return structured model output."""


class DeterministicSummaryModel:
    """Test fixture summary model; defaults to source-backed local output."""

    def __init__(self, outputs: list[dict[str, Any]] | None = None) -> None:
        self._outputs = list(outputs or [])
        self.prompt_versions: list[str] = []

    def generate(
        self,
        *,
        meeting_id: str,
        cues: list[CaptionCue],
        prompt_version: str,
    ) -> dict[str, Any]:
        self.prompt_versions.append(prompt_version)
        if self._outputs:
            return self._outputs.pop(0)
        facts = extract_quantitative_facts(cues)
        if facts:
            fact = facts[0]
            return {
                "narrative": fact.text,
                "sourced_claims": [
                    {
                        "claim_id": "claim-1",
                        "text": fact.text,
                        "claim_type": "quantitative",
                        "transcript_ranges": [fact.source_range.model_dump()],
                    }
                ],
            }
        if cues:
            cue = cues[0]
            return {
                "narrative": cue.text,
                "sourced_claims": [
                    {
                        "claim_id": "claim-1",
                        "text": cue.text,
                        "claim_type": "narrative",
                        "transcript_ranges": [
                            {
                                "cue_id": cue.cue_id,
                                "start_seconds": cue.start_seconds,
                                "end_seconds": cue.end_seconds,
                            }
                        ],
                    }
                ],
            }
        return {"narrative": "", "sourced_claims": []}


def _normalise_claims(
    raw_claims: list[Any], cues: list[CaptionCue]
) -> tuple[list[SourcedClaim], int]:
    """Repair harmless format slips; drop claims whose evidence is not in the transcript.

    - A numeric ``claim_id`` (Gemma 4 writes 1, 2, 3) becomes its string form.
    - A range that overlaps the caption line it cites but runs past it (spans
      into the next line, or a rounding slip) becomes exactly that line.
    - A range citing a line that does not exist, or a time that does not touch
      the cited line, is removed; a claim left with no range is removed.
    Returns the surviving claims and how many were removed. Every surviving
    range lies inside a committed cue, so the validator's guarantee is unchanged.
    """

    by_id = {cue.cue_id: cue for cue in cues}
    kept: list[SourcedClaim] = []
    removed = 0
    for raw in raw_claims:
        if not isinstance(raw, dict):
            removed += 1
            continue
        claim = dict(raw)
        claim_id = claim.get("claim_id")
        if isinstance(claim_id, int) and not isinstance(claim_id, bool):
            claim["claim_id"] = str(claim_id)
        ranges = claim.get("transcript_ranges")
        if isinstance(ranges, list) and ranges:
            fixed = [r for r in (_fit_range(item, by_id, cues) for item in ranges) if r is not None]
            if not fixed:
                removed += 1
                continue
            claim["transcript_ranges"] = fixed
        try:
            kept.append(SourcedClaim.model_validate(claim))
        except ValidationError:
            removed += 1
    return kept, removed


_TEXT_TIMES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)")
_TEXT_CUE_RE = re.compile(r"[A-Za-z][\w.:-]*\d")


def _read_range(item: Any) -> tuple[str | None, float | None, float | None]:
    """Cue id and times from a range object, or from a range written as text.

    Models often copy the transcript line's own format ("cue-000002 [4.00-9.00]",
    "[4.00-9.00]") instead of the object form.
    """

    start: float | None
    end: float | None
    if isinstance(item, dict):
        cue_id = item.get("cue_id")
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
        except (KeyError, TypeError, ValueError):
            start = end = None
        return (str(cue_id) if cue_id is not None else None), start, end
    if isinstance(item, str):
        times = _TEXT_TIMES_RE.search(item)
        without_times = _TEXT_TIMES_RE.sub(" ", item)
        cue = _TEXT_CUE_RE.search(without_times)
        start, end = (float(times.group(1)), float(times.group(2))) if times else (None, None)
        return (cue.group(0) if cue else None), start, end
    return None, None, None


# The transcript shows cue times to 2 decimals; a copied time is within rounding.
_EXACT_TIME_TOLERANCE_SECONDS = 0.01


def _overlap(cue: CaptionCue, start: float, end: float) -> float:
    return min(end, cue.end_seconds) - max(start, cue.start_seconds)


def _fit_range(
    item: Any, by_id: dict[str, CaptionCue], cues: list[CaptionCue]
) -> dict[str, Any] | None:
    """Locate one citation on a committed caption line, or ``None``.

    - A named line that does not exist: removed (an invented citation).
    - Times that overlap the named line: clipped to that line.
    - Times that miss the named line: moved only to a line whose start and end
      they match exactly (Gemma 4 often copies the right line's times under the
      previous line's label); otherwise removed.
    - Times with no line named: the line they overlap most.
    - A line named with no times: that whole line.
    Every result lies exactly inside a committed cue.
    """

    cue_id, start, end = _read_range(item)
    named = by_id.get(cue_id) if cue_id else None
    if cue_id and named is None:
        return None
    if start is None or end is None or end <= start:
        if named is None:
            return None
        cue = named
    elif named is not None and _overlap(named, start, end) > 0:
        cue = named
    elif named is not None:
        exact = next(
            (
                c
                for c in cues
                if abs(c.start_seconds - start) <= _EXACT_TIME_TOLERANCE_SECONDS
                and abs(c.end_seconds - end) <= _EXACT_TIME_TOLERANCE_SECONDS
            ),
            None,
        )
        if exact is None:
            return None
        cue = exact
    else:
        best = max(cues, key=lambda candidate: _overlap(candidate, start, end), default=None)
        if best is None or _overlap(best, start, end) <= 0:
            return None
        cue = best
    lo, hi = cue.start_seconds, cue.end_seconds
    if start is not None and end is not None and end > start and cue is named:
        lo, hi = max(start, lo), min(end, hi)
    return {"cue_id": cue.cue_id, "start_seconds": lo, "end_seconds": hi}


def _removed_claims_message(removed: int) -> str | None:
    if not removed:
        return None
    what = (
        "1 claim was removed because its transcript citation"
        if removed == 1
        else f"{removed} claims were removed because their transcript citations"
    )
    return (
        f"{what} did not match the transcript. "
        "Check the summary text against the recording before approving."
    )


class SummaryGenerationPipeline:
    """Generate, validate, retry once, and fail closed on unsupported claims."""

    def __init__(self, model: SummaryModel | None = None) -> None:
        self._model = model or OllamaSummaryModel.for_release()

    def generate(self, *, meeting_id: str, cues: list[CaptionCue]) -> SummaryDraft:
        errors: list[str] = []
        for prompt_version in (PROMPT_VERSION, RETRY_PROMPT_VERSION):
            output = self._model.generate(
                meeting_id=meeting_id,
                cues=cues,
                prompt_version=prompt_version,
            )
            try:
                narrative = str(output["narrative"]).strip()
                if not narrative:
                    # An unreadable answer is parsed to an empty summary; never
                    # send a blank summary to staff as if it were a draft.
                    raise UnsupportedSummaryClaimError("the model returned an empty narrative")
                raw_claims = output["sourced_claims"]
                if not isinstance(raw_claims, list):
                    raise TypeError("sourced_claims must be a list")
                claims, removed = _normalise_claims(raw_claims, cues)
                if raw_claims and not claims:
                    raise UnsupportedSummaryClaimError(
                        "no sourced claim cited a committed transcript cue"
                    )
                facts = extract_quantitative_facts(cues)
                has_quantitative_claim = any(claim.claim_type == "quantitative" for claim in claims)
                if facts and not has_quantitative_claim:
                    raise UnsupportedSummaryClaimError(
                        "quantitative transcript facts require sourced timestamp evidence"
                    )
                SourcedClaimValidator(cues).validate_claims(claims)
                return self._draft(
                    meeting_id=meeting_id,
                    cues=cues,
                    status="pending_review",
                    narrative=narrative,
                    sourced_claims=claims,
                    prompt_version=prompt_version,
                    operator_message=_removed_claims_message(removed),
                )
            except (KeyError, TypeError, ValidationError, UnsupportedSummaryClaimError) as exc:
                errors.append(str(exc))

        return self._draft(
            meeting_id=meeting_id,
            cues=cues,
            status="refused",
            narrative="",
            sourced_claims=[],
            prompt_version=RETRY_PROMPT_VERSION,
            operator_message=(
                "Summary refused because the model output could not be tied to "
                "committed transcript timestamp evidence. Review the transcript "
                "and regenerate after correcting missing or ambiguous cues."
            ),
            errors=errors,
        )

    def _draft(
        self,
        *,
        meeting_id: str,
        cues: list[CaptionCue],
        status: str,
        narrative: str,
        sourced_claims: list[SourcedClaim],
        prompt_version: str,
        operator_message: str | None,
        errors: list[str] | None = None,
    ) -> SummaryDraft:
        # Record the provenance of the model that actually ran. Real adapters
        # (OllamaSummaryModel, CloudSummaryModel) expose ``model_tag`` and, for
        # the local path, a ``provenance`` with the live manifest digest and
        # runtime version; the pipeline previously discarded all of it and
        # stamped every persisted, records-bound draft with a fixture tag, so
        # the durable audit fingerprint misstated which model produced an
        # operator-approved summary. Only a genuine test fixture (which exposes
        # no ``model_tag``) falls back to the fixture label.
        model = self._model
        model_provenance = getattr(model, "provenance", None)
        provenance = ModelProvenance(
            model_tag=getattr(model, "model_tag", None) or "fixture-summary-no-real-model",
            model_digest=getattr(model_provenance, "digest", None),
            ollama_version=getattr(model_provenance, "ollama_version", None),
            prompt_version=prompt_version,
            extraction_version=EXTRACTION_VERSION,
            runtime_parameters={
                "temperature": 0,
                "source_transcript_fingerprint": transcript_fingerprint(cues),
            },
            generated_at=datetime.now(UTC),
        )
        summary_id = f"summary-{uuid4().hex}"
        fingerprint = sha256_fingerprint(
            {
                "meeting_id": meeting_id,
                "status": status,
                "narrative": narrative,
                "sourced_claims": [claim.model_dump(mode="json") for claim in sourced_claims],
                "provenance": provenance.model_dump(mode="json"),
                "errors": errors or [],
            }
        )
        return SummaryDraft(
            summary_id=summary_id,
            meeting_id=meeting_id,
            status=status,  # type: ignore[arg-type]
            narrative=narrative,
            sourced_claims=sourced_claims,
            provenance=provenance,
            audit_fingerprint=fingerprint,
            operator_message=operator_message,
        )
