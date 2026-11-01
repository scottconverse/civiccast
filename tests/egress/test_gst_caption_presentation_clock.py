"""Pop-on memory loading and display are different clocks; never fit decoded offsets."""

import json
import threading
from pathlib import Path
from unittest.mock import patch

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
