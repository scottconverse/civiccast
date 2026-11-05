"""F11 (RC4 field run 2026-11-04): the live caption proof failed ~half its samples on healthy stations.

Measured on the Beelink and the host by replaying the shipped proof against live channels:
1. the delivery receipt was discarded whenever a page was still loading (pending_page_count != 0),
   which is the normal state about half the time -> NO_EXPECTED_CUES with correct captions on air;
2. a 6 s capture often held no complete page -> NO_EXPECTED_CUES;
3. a capture that joins while a pop-on page is loading decodes a partial page whose leftover text
   is glued onto the next page -> both MISMATCH, although the emitted stream is correct.
"""

from __future__ import annotations

import json
from pathlib import Path

from civiccast.captions.models import CaptionCue
from civiccast.egress.caption_proof import (
    DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS,
    DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS,
    cues_cleanly_inside_capture,
)
from civiccast.egress.caption_proof_process import CaptionProofProcessSupervisor
from civiccast.egress.caption_proof_worker import read_presentation_receipt

_CLOCK = "cea608-display-control-pts-plus-mpegts-base"


def _receipt(path: Path, *, pending: int, pages: list[dict]) -> None:
    displayed = len(pages)
    path.write_text(
        json.dumps(
            {
                "clock": _CLOCK,
                "pages": pages,
                "encoded_page_count": displayed + pending,
                "displayed_page_count": displayed,
                "retired_page_count": 0,
                "pending_page_count": pending,
                "active_page_count": 0,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )


def _page(text: str, start: float, end: float) -> dict:
    return {"text": text, "start_seconds": start, "end_seconds": end}


def _cue(n: int, start: float, end: float) -> CaptionCue:
    return CaptionCue(
        cue_id=f"c{n}",
        start_seconds=start,
        end_seconds=end,
        text=f"PAGE {n}",
        confidence=1.0,
        low_confidence=False,
    )


def test_receipt_read_waits_out_a_page_that_is_still_loading(tmp_path: Path) -> None:
    receipt = tmp_path / "presentation.json"
    pages = [_page("ONE", 100.0, 102.0), _page("TWO", 102.0, 104.5)]
    _receipt(receipt, pending=1, pages=pages)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        _receipt(receipt, pending=0, pages=pages)  # the loading page has now displayed

    cues = read_presentation_receipt(receipt, channel_id="gov", sleep=fake_sleep)
    assert [c.text for c in cues] == ["ONE", "TWO"]
    assert len(sleeps) == 1


def test_receipt_that_never_settles_still_fails_closed_after_bounded_reads(tmp_path: Path) -> None:
    receipt = tmp_path / "presentation.json"
    _receipt(receipt, pending=1, pages=[_page("ONE", 100.0, 102.0)])
    sleeps: list[float] = []
    assert read_presentation_receipt(receipt, channel_id="gov", sleep=sleeps.append) == []
    # Bounded: a stuck queue must not hold the proof loop, and must not be read as settled.
    assert 1 <= len(sleeps) <= 10
    assert sum(sleeps) <= 6.0


def test_receipt_with_errors_or_wrong_clock_is_refused_without_waiting(tmp_path: Path) -> None:
    receipt = tmp_path / "presentation.json"
    receipt.write_text(json.dumps({"clock": "other", "pages": []}), encoding="utf-8")
    sleeps: list[float] = []
    assert read_presentation_receipt(receipt, channel_id="gov", sleep=sleeps.append) == []
    assert sleeps == []


def test_only_pages_loaded_entirely_inside_the_capture_are_expected() -> None:
    # Capture covers [1000, 1018]. Page N can only start loading after page N-1 displays, so page N
    # is cleanly contained iff page N-2 displayed at/after the capture origin.
    cues = [
        _cue(0, 998.0, 1000.5),  # displayed before origin
        _cue(1, 1000.5, 1003.0),  # its load began before origin -> partial
        _cue(2, 1003.0, 1005.0),  # N-2 = cue0 displayed before origin -> may hold cue1 leftovers
        _cue(3, 1005.0, 1008.0),  # N-2 = cue1 displayed at 1000.5 >= origin -> clean
        _cue(4, 1008.0, 1016.0),  # clean
        _cue(5, 1016.0, 1019.0),  # ends after the capture -> not complete
    ]
    kept = cues_cleanly_inside_capture(cues, origin=1000.0, end=1018.0)
    assert [c.text for c in kept] == ["PAGE 3", "PAGE 4"]
    # Times are made relative to the capture origin, as the decoded side is.
    assert kept[0].start_seconds == 5.0 and kept[0].end_seconds == 8.0


def test_clean_containment_survives_the_33_bit_transport_clock_wrap() -> None:
    wrap = (1 << 33) / 90000.0
    cues = [_cue(0, wrap - 4.0, wrap - 2.0), _cue(1, wrap - 2.0, wrap - 1.0), _cue(2, 0.5, 2.0)]
    # The capture origin is just after the wrap; cue 0 and cue 1 precede it.
    assert cues_cleanly_inside_capture(cues, origin=0.2, end=10.0) == []
    kept = cues_cleanly_inside_capture(cues, origin=wrap - 5.0, end=wrap + 5.0)
    assert [c.text for c in kept] == ["PAGE 2"]


def test_production_capture_is_long_enough_to_hold_a_cleanly_loaded_page() -> None:
    # Pages run 1-3 s; three consecutive pages must fit, and -re reads in real time.
    assert DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS >= 18.0
    assert DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS >= DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS + 15.0
    command = CaptionProofProcessSupervisor(poll_seconds=30.0)._command()
    assert (
        command[command.index("--capture-seconds") + 1]
        == f"{DEFAULT_CAPTION_PROOF_CAPTURE_SECONDS}"
    )
    assert (
        command[command.index("--timeout-seconds") + 1]
        == f"{DEFAULT_CAPTION_PROOF_TIMEOUT_SECONDS}"
    )
