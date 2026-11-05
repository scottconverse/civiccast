# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Optional per-device element properties (mfh26xenc gop-size)."""

from __future__ import annotations

from civiccast.egress.gst.optional_props import should_set_property


def test_mf_gop_size_is_skipped_when_the_device_does_not_expose_it() -> None:
    assert should_set_property("mfh264enc", "gop-size", lambda _k: False) is False
    assert should_set_property("mfh265enc", "gop-size", lambda _k: False) is False


def test_mf_gop_size_is_set_when_the_device_exposes_it() -> None:
    assert should_set_property("mfh264enc", "gop-size", lambda _k: True) is True


def test_every_other_property_is_always_set_so_a_missing_one_still_fails_loudly() -> None:
    assert should_set_property("nvh264enc", "gop-size", lambda _k: False) is True
    assert should_set_property("mfh264enc", "bitrate", lambda _k: False) is True
