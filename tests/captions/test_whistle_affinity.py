# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Whistle speech child runs on the performance cores of a hybrid CPU."""

import inspect
import logging
import struct

import civiccast.captions.whistle as whistle


def core_record(efficiency_class: int, group: int, mask: int) -> bytes:
    """Build one RelationProcessorCore record as Windows lays it out on x64."""
    body = struct.pack("<BB20sH", 0, efficiency_class, b"\0" * 20, 1)
    body += struct.pack("<QH6s", mask, group, b"\0" * 6)
    header = struct.pack("<II", 0, 8 + len(body))
    return header + body


def select(records, group_count=1):
    return whistle._select_performance_core_mask(records, group_count)


def test_hybrid_layout_masks_the_highest_class_only():
    records = [(1, 0, 0b1111), (0, 0, 0b11_0000)]
    mask, reason = select(records)
    assert mask == 0b1111
    assert reason == ""


def test_parser_reads_efficiency_class_group_and_mask():
    buffer = core_record(1, 0, 0b1011) + core_record(0, 0, 0b1100_0000)
    assert whistle._parse_processor_core_records(buffer) == [
        (1, 0, 0b1011),
        (0, 0, 0b1100_0000),
    ]


def test_non_hybrid_layout_makes_no_change():
    mask, reason = select([(0, 0, 0b1111), (0, 0, 0b11_0000)])
    assert mask == 0
    assert "not hybrid" in reason


def test_highest_class_with_fewer_than_four_logical_cpus_makes_no_change():
    mask, reason = select([(1, 0, 0b111), (0, 0, 0b11000)])
    assert mask == 0
    assert "fewer than 4" in reason


def test_more_than_one_processor_group_makes_no_change():
    mask, reason = select([(1, 0, 0b1111), (0, 0, 0b11_0000)], group_count=2)
    assert mask == 0
    assert "processor group" in reason


class AffinityKernel:
    def __init__(self, succeeds=True):
        self.calls = []

        def set_process_affinity_mask(handle, mask):
            self.calls.append((handle, mask))
            return 1 if succeeds else 0

        self.SetProcessAffinityMask = set_process_affinity_mask


def install(monkeypatch, kernel, detected=(0b1111, "")):
    def detect():
        return detected

    monkeypatch.setattr(whistle, "_kernel32", lambda: kernel)
    monkeypatch.setattr(whistle, "_detect_performance_core_mask", detect)


def test_whistle_child_gets_the_performance_core_mask(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    kernel = AffinityKernel()
    install(monkeypatch, kernel)
    whistle._apply_performance_core_affinity("whistle", 4321, 0x1234)
    assert kernel.calls == [(0x1234, 0b1111)]
    assert "engine=whistle pid=4321" in caplog.text
    assert "cpus=[0, 1, 2, 3]" in caplog.text


def test_non_hybrid_logs_info_and_skips_the_affinity_call(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    kernel = AffinityKernel()
    install(monkeypatch, kernel, detected=(0, "CPU is not hybrid"))
    whistle._apply_performance_core_affinity("whistle", 4321, 0x1234)
    assert kernel.calls == []
    assert "reason=CPU is not hybrid" in caplog.text


def test_detection_failure_warns_and_does_not_raise(monkeypatch, caplog):
    kernel = AffinityKernel()

    def boom():
        raise OSError("GetLogicalProcessorInformationEx failed")

    monkeypatch.setattr(whistle, "_kernel32", lambda: kernel)
    monkeypatch.setattr(whistle, "_detect_performance_core_mask", boom)
    whistle._apply_performance_core_affinity("whistle", 4321, 0x1234)
    assert kernel.calls == []
    assert "WARNING" in caplog.text or caplog.records
    assert "GetLogicalProcessorInformationEx failed" in caplog.text


def test_failed_affinity_call_warns_and_does_not_raise(monkeypatch, caplog):
    kernel = AffinityKernel(succeeds=False)
    install(monkeypatch, kernel)
    whistle._apply_performance_core_affinity("whistle", 4321, 0x1234)
    assert kernel.calls
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_whisper_engine_makes_no_affinity_call(monkeypatch, caplog):
    kernel = AffinityKernel()
    install(monkeypatch, kernel)
    whistle._apply_performance_core_affinity("whisper", 4321, 0x1234)
    assert kernel.calls == []


def test_affinity_is_applied_before_the_resume_call():
    source = inspect.getsource(whistle._SpeechProcess.__init__)
    assert source.index("_apply_performance_core_affinity") < source.index("ResumeThread")


def test_child_handle_requests_process_set_information():
    assert whistle.SPEECH_CHILD_PROCESS_ACCESS == 0x701
    source = inspect.getsource(whistle._SpeechProcess.__init__)
    assert "SPEECH_CHILD_PROCESS_ACCESS" in source
