"""Pop-on memory loading and display are different clocks; never fit decoded offsets."""

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from civiccast.egress.gst.caption_clock import CaptionDeliveryClock


def test_presentation_uses_eoc_and_erase_not_appsrc_loading_pts(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.record("FIRST PAGE", 10, 2)
    clock.observe_text("FIRST PAGE", program="")
    clock.observe_cc_data(b"\xfc\x94\x20", 10, program="")
    clock.observe_cc_data(b"\xfc\x94\x2f", 10.7, program="")
    clock.observe_cc_data(b"\xfc\x94\x2f", 10.733, program="")  # duplicate control
    clock.observe_cc_data(b"\xfc\x94\x2c", 13.1, program="")
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["pages"] == [
        {"text": "FIRST PAGE", "start_seconds": 3610.7, "end_seconds": 3613.1}
    ]
    assert result["encoded_page_count"] == result["displayed_page_count"] == 1
    assert result["active_page_count"] == result["pending_page_count"] == 0
    assert result["errors"] == []
    assert json.loads(clock.path.read_text())[0]["start_seconds"] == 3610


def test_unfinished_page_is_not_a_completed_presentation_or_false_timing_pass(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_text("UNFINISHED", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 5, program="")
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["pages"] == []
    assert result["active_page_count"] == result["displayed_page_count"] == 1


def test_incoming_program_clear_closes_display_but_retired_branch_cannot_add_pages(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_text("OUTGOING", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 1, program="")
    clock.begin_program("next")
    clock.observe_text("STALE", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 8, program="")
    clock.observe_cc_data(b"\xfc\x94\x2c", 9, program="next")
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["pages"] == [{"text": "OUTGOING", "start_seconds": 3601, "end_seconds": 3609}]
    assert result["encoded_page_count"] == result["displayed_page_count"] == 1
    assert result["errors"] == []


def test_invalid_encoder_clock_is_visible_and_never_silently_accepted(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_cc_data(b"\xfc\x94", 2, program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 3, program="")
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert len(result["errors"]) == 2
    assert result["pages"] == []


@pytest.mark.parametrize("display_start", [39.966, 40.1])
def test_program_cutoff_retires_active_encoder_lookahead(tmp_path, display_start):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_text("OUTGOING LOOKAHEAD", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", display_start, program="")
    clock.begin_program("next", cutoff_seconds=39.966)
    clock.observe_cc_data(b"\xfc\x94\x2c", 39.966, program="next")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 45, program="")
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["pages"] == []
    assert result["encoded_page_count"] == result["retired_page_count"] == 1
    assert result["displayed_page_count"] == result["active_page_count"] == 0
    assert result["errors"] == []


def test_program_cutoff_keeps_earlier_active_display_until_incoming_erase(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_text("VISIBLE OUTGOING", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 38, program="")
    clock.begin_program("next", cutoff_seconds=39.966)
    clock.observe_cc_data(b"\xfc\x94\x2c", 39.966, program="next")
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["pages"] == [
        {"text": "VISIBLE OUTGOING", "start_seconds": 3638, "end_seconds": 3639.966}
    ]
    assert result["displayed_page_count"] == 1
    assert result["retired_page_count"] == 0
    assert result["errors"] == []


def test_program_cutoff_trims_completed_encoder_lookahead_and_retires_future_pages(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    for text, start, end in [("VISIBLE", 38, 41), ("FUTURE", 42, 44)]:
        clock.observe_text(text, program="")
        clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", start, program="")
        clock.observe_cc_data(b"\xfc\x94\x2c", end, program="")
    clock.begin_program("next", cutoff_seconds=40)
    clock.observe_cc_data(b"\xfc\x94\x2c", 40, program="next")
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["pages"] == [{"text": "VISIBLE", "start_seconds": 3638, "end_seconds": 3640}]
    assert result["encoded_page_count"] == 2
    assert result["displayed_page_count"] == result["retired_page_count"] == 1
    assert result["errors"] == []


@pytest.mark.parametrize("erase", [4, 5])
def test_same_program_nonincreasing_display_clock_still_fails(tmp_path, erase):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_text("PAGE", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 5, program="")
    clock.observe_cc_data(b"\xfc\x94\x2c", erase, program="")
    clock.begin_program("next", cutoff_seconds=10)
    clock.flush_presentation()
    result = json.loads(clock.presentation_path.read_text())
    assert result["errors"] == ["non-increasing caption display clock"]
    assert result["pages"] == []


@pytest.mark.parametrize("cutoff", [-1, float("nan"), float("inf")])
def test_invalid_program_reset_clock_remains_a_failure(tmp_path, cutoff):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.begin_program("next", cutoff_seconds=cutoff)
    clock.flush_presentation()
    assert json.loads(clock.presentation_path.read_text())["errors"] == [
        "invalid caption program reset clock"
    ]


def test_backwards_program_cutoff_preserves_history_counts_and_program(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_text("COMPLETED", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 8, program="")
    clock.observe_cc_data(b"\xfc\x94\x2c", 9, program="")
    clock.begin_program("current", cutoff_seconds=10)
    clock.observe_text("ACTIVE", program="current")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 11, program="current")
    clock.flush_presentation()
    before = json.loads(clock.presentation_path.read_text())

    clock.begin_program("backwards", cutoff_seconds=7)
    clock.observe_text("REFUSED TOKEN", program="backwards")
    clock.flush_presentation()
    after = json.loads(clock.presentation_path.read_text())
    assert after.pop("errors") == ["backwards caption program reset clock"]
    before.pop("errors")
    assert after == before
    clock.flush_presentation()
    assert json.loads(clock.presentation_path.read_text())["errors"] == [
        "backwards caption program reset clock"
    ]


def test_production_expectations_refuse_pending_loss_but_allow_explicit_fencing(tmp_path):
    from civiccast.egress.caption_proof_worker import build_caption_proof_worker

    clock = CaptionDeliveryClock(tmp_path / "government/captions/delivered.json")
    clock.observe_text("DELIVERED", program="")
    clock.observe_cc_data(b"\xfc\x94\x20\xfc\x94\x2f", 1, program="")
    clock.observe_cc_data(b"\xfc\x94\x2c", 3, program="")
    clock.observe_text("NEVER DISPLAYED", program="")
    clock.flush_presentation()
    with patch("civiccast.egress.store.PostgresEgressStore", return_value=object()):
        worker = build_caption_proof_worker(lambda: None, work_dir=tmp_path)
    assert worker._expected_cues_provider("government") == []
    clock.begin_program("next")
    clock.flush_presentation()
    assert [cue.text for cue in worker._expected_cues_provider("government")] == ["DELIVERED"]
    assert json.loads(clock.presentation_path.read_text())["retired_page_count"] == 1


def test_receipt_io_does_not_block_media_or_lose_concurrent_observations(tmp_path):
    clock = CaptionDeliveryClock(tmp_path / "delivered.json")
    clock.observe_text("FIRST", program="")
    original_write = Path.write_text
    observed = threading.Event()
    threads = []

    def during_write(path, data, **kwargs):
        def observe():
            clock.observe_text("SECOND", program="")
            observed.set()

        thread = threading.Thread(target=observe)
        threads.append(thread)
        thread.start()
        assert observed.wait(1), "media callback blocked behind receipt I/O"
        return original_write(path, data, **kwargs)

    try:
        with patch.object(Path, "write_text", during_write):
            clock.flush_presentation()
    finally:
        for thread in threads:
            thread.join(timeout=2)
    assert json.loads(clock.presentation_path.read_text())["encoded_page_count"] == 1
    clock.flush_presentation()
    assert json.loads(clock.presentation_path.read_text())["encoded_page_count"] == 2


def test_cea608_apostrophe_rendering_does_not_hide_text_or_timing_loss(tmp_path):
    from civiccast.captions.models import CaptionCue
    from civiccast.egress.caption_embed import evaluate_caption_decode_back

    expected = CaptionCue(
        cue_id="expected", text="that's it", start_seconds=1, end_seconds=3, confidence=1.0
    )
    decoded = expected.model_copy(update={"cue_id": "decoded", "text": "that\u2018s it"})

    def evaluate(cue):
        return evaluate_caption_decode_back(
            channel_id="government",
            emitted_stream_path=tmp_path / "out.ts",
            expected_cues=[expected],
            decoded_cues=[cue],
            decoder_name="ffmpeg-subcc",
        ).status

    assert evaluate(decoded) == "PASS"
    assert evaluate(decoded.model_copy(update={"text": "thats it"})) == "FAIL"
    assert evaluate(decoded.model_copy(update={"end_seconds": 5})) == "FAIL"
