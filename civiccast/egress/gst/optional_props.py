# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Element properties the engine may skip when a device does not expose them.

Media Foundation encoders install some properties per device: ``gst-inspect-1.0
mfh264enc`` lists ``gop-size`` as "conditionally available" (only when the
device's encoder MFT supports it). ``bridge._apply_encoder_fixups`` asks every
hardware encoder for a bounded keyframe interval; on an MF device without that
control, setting the property would raise and the channel would never start.
Exactly these (factory, property) pairs are skipped -- with a warning -- when
missing; every other property still fails loudly, as before.
"""

from __future__ import annotations

from collections.abc import Callable

OPTIONAL_ELEMENT_PROPS: frozenset[tuple[str, str]] = frozenset(
    {
        ("mfh264enc", "gop-size"),
        ("mfh265enc", "gop-size"),
    }
)


def should_set_property(factory: str, key: str, has_property: Callable[[str], bool]) -> bool:
    """True when ``key`` must be set on the element (missing => the caller's error).

    False only for a listed optional property the element does not expose.
    """
    if (factory, key) not in OPTIONAL_ELEMENT_PROPS:
        return True
    return has_property(key)
