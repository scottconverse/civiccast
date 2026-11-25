# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""One station logo image per channel, stored by the service (issue #348).

An operator uploads a logo once; the service owns the file and its fixed
location under the channel's own egress work directory::

    <egress work dir>/<channel_id>/branding/logo.png

The operator never types a path. The same file feeds both logo paths:

* the station bug on the program picture (``civiccast.egress.gst.bridge``), and
* a CG board logo zone whose ``image_asset_ref`` is the sentinel value
  ``station-logo`` (resolved through :func:`with_station_logo`).

Validation is deliberately strict but self-contained: an image must have a
real PNG or JPEG header, and it must decode. The repo has no station-runtime
image-decoding dependency (Pillow is only a documentation-renderer dependency),
so decoding is done by ``ffprobe``, which this product already uses and ships
(``civiccast/egress/continuity.py``, ``civiccast/egress/hls_relay.py``). The
file name stays ``logo.png`` whatever the upload format, because both
GStreamer's ``decodebin`` and ffmpeg sniff the content and ignore the suffix.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from civiccast.cg.board_resolver import ResolvedBoard

#: Same contract as ``civiccast.egress.board_compositor.ImageResolver``.
type ImageResolver = Callable[[str], Path | None]

#: The ``image_asset_ref`` value a CG board logo zone uses to mean "this
#: channel's uploaded station logo".
STATION_LOGO_REF = "station-logo"

#: Operator-facing upload limits, stated in the upload error messages.
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_WIDTH = 1920
MAX_LOGO_HEIGHT = 1080

#: Fixed file name. The suffix is only a label for a human browsing the
#: folder -- decoders sniff the bytes.
LOGO_FILENAME = "logo.png"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_FFPROBE_EXECUTABLE = "ffprobe"
_FFPROBE_TIMEOUT_SECONDS = 15.0

#: ffprobe's codec name for each accepted container.
_ACCEPTED_CODECS = frozenset({"png", "mjpeg"})


class StationLogoError(ValueError):
    """An uploaded image was refused; the message is safe to show an operator."""


class StationLogoCheckerUnavailableError(RuntimeError):
    """The station's image decoder (ffprobe) is missing, so uploads cannot be checked."""


def station_logo_dir(work_dir: Path | str, channel_id: str) -> Path:
    """The per-channel branding directory the service owns."""

    return Path(work_dir).expanduser() / channel_id / "branding"


def station_logo_path(work_dir: Path | str, channel_id: str) -> Path:
    """The fixed on-disk location of this channel's station logo."""

    return station_logo_dir(work_dir, channel_id) / LOGO_FILENAME


def station_logo_path_in_channel_dir(channel_dir: Path | str) -> Path:
    """Same location as :func:`station_logo_path`, given an already per-channel dir.

    The playout-graph builder (``civiccast.egress.gst.bridge``) is handed the
    channel's own work directory, not the station-wide one.
    """

    return Path(channel_dir).expanduser() / "branding" / LOGO_FILENAME


def station_logo_bytes(work_dir: Path | str, channel_id: str) -> int | None:
    """Size of the uploaded logo in bytes, or ``None`` when none is uploaded."""

    try:
        return station_logo_path(work_dir, channel_id).stat().st_size
    except OSError:
        return None


def station_logo_updated_at(work_dir: Path | str, channel_id: str) -> float | None:
    """Last-write time (epoch seconds) of the uploaded logo, or ``None``."""

    try:
        return station_logo_path(work_dir, channel_id).stat().st_mtime
    except OSError:
        return None


def station_logo_present(work_dir: Path | str, channel_id: str) -> bool:
    return station_logo_bytes(work_dir, channel_id) is not None


def validate_logo_bytes(data: bytes) -> tuple[str, int, int]:
    """Check an uploaded image is a real, decodable PNG or JPEG within limits.

    Returns ``(format, width, height)``. Raises :class:`StationLogoError` with a
    plain message the operator can act on, or
    :class:`StationLogoCheckerUnavailableError` when ffprobe is not installed.
    """

    if not data:
        raise StationLogoError("The selected file is empty. Choose a PNG or JPEG image.")
    if len(data) > MAX_LOGO_BYTES:
        raise StationLogoError(
            "That image is larger than 2 MB. Save a smaller copy (2 MB or less) and try again."
        )
    if data.startswith(_PNG_MAGIC):
        declared_format = "png"
    elif data.startswith(_JPEG_MAGIC):
        declared_format = "jpeg"
    else:
        raise StationLogoError(
            "That file is not a PNG or JPEG image. Upload a PNG (.png) or JPEG (.jpg) file."
        )

    ffprobe = shutil.which(_FFPROBE_EXECUTABLE)
    if ffprobe is None:
        raise StationLogoCheckerUnavailableError(
            "This station's image checker is not available, so the logo cannot be checked. "
            "Please contact support."
        )

    # Decode the bytes, don't just trust the header: a truncated or corrupt
    # file would otherwise reach the playout pipeline and show nothing.
    with tempfile.TemporaryDirectory(prefix="civiccast-logo-check-") as scratch:
        sample = Path(scratch) / ("logo.png" if declared_format == "png" else "logo.jpg")
        sample.write_bytes(data)
        completed = subprocess.run(  # noqa: S603 - list form, no shell, fixed binary
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "json",
                str(sample),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_FFPROBE_TIMEOUT_SECONDS,
            check=False,
        )
    if completed.returncode != 0:
        raise StationLogoError(
            "That image could not be read. It may be damaged; try saving and uploading it again."
        )
    try:
        streams = json.loads(completed.stdout or "{}").get("streams") or []
        stream = streams[0]
        codec = str(stream.get("codec_name") or "")
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (ValueError, TypeError, IndexError, KeyError, AttributeError) as exc:
        raise StationLogoError(
            "That image could not be read. It may be damaged; try saving and uploading it again."
        ) from exc
    if codec not in _ACCEPTED_CODECS:
        raise StationLogoError(
            "That file is not a PNG or JPEG image. Upload a PNG (.png) or JPEG (.jpg) file."
        )
    if width <= 0 or height <= 0:
        raise StationLogoError("That image has no readable size. Try saving it again.")
    if width > MAX_LOGO_WIDTH or height > MAX_LOGO_HEIGHT:
        raise StationLogoError(
            f"That image is {width} x {height} pixels; the largest allowed is "
            f"{MAX_LOGO_WIDTH} x {MAX_LOGO_HEIGHT}. Save a smaller copy and try again."
        )
    return ("png" if codec == "png" else "jpeg"), width, height


def write_station_logo(work_dir: Path | str, channel_id: str, data: bytes) -> Path:
    """Validate ``data`` and atomically replace this channel's logo file."""

    validate_logo_bytes(data)
    target = station_logo_path(work_dir, channel_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp file first so a crash or a concurrent upload can
    # never leave a partial image where the playout path reads one.
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".logo-", suffix=".tmp", delete=False
    ) as handle:
        staging = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            staging.replace(target)
        except BaseException:
            with contextlib.suppress(OSError):
                staging.unlink()
            raise
    return target


def remove_station_logo(work_dir: Path | str, channel_id: str) -> bool:
    """Delete the channel's logo. Returns whether a file was removed."""

    try:
        station_logo_path(work_dir, channel_id).unlink()
    except FileNotFoundError:
        return False
    return True


def resolve_station_logo(work_dir: Path | str, channel_id: str) -> Path | None:
    """The uploaded logo's path, or ``None`` when this channel has none."""

    path = station_logo_path(work_dir, channel_id)
    return path if path.is_file() else None


def logo_media_type(path: Path | str) -> str:
    """The HTTP content type for a stored logo (defaults to PNG)."""

    try:
        with Path(path).open("rb") as handle:
            head = handle.read(3)
    except OSError:
        return "image/png"
    return "image/jpeg" if head.startswith(_JPEG_MAGIC) else "image/png"


def logo_file_looks_like_an_image(path: Path | str) -> bool:
    """Cheap build-time guard: does this file still start like a PNG or JPEG?

    Uploads are fully decoded before they are stored, but a file can be
    damaged later (a failed copy, a full disk). The playout path checks this
    header so a bad file falls back to "no logo" instead of being handed to
    the pipeline. Full decoding on every pipeline build would spawn a process
    for nothing; this is the cheap half of the same check.
    """

    try:
        with Path(path).open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    return head.startswith(_PNG_MAGIC) or head.startswith(_JPEG_MAGIC)


def board_uses_station_logo(board: ResolvedBoard | None) -> bool:
    """Whether any zone on the resolved board shows the station logo."""

    if board is None:
        return False
    return any(
        zone.content.get("image_asset_ref") == STATION_LOGO_REF for zone in board.snapshot.zones
    )


def station_logo_version(board: ResolvedBoard | None, work_dir: Path | str, channel_id: str) -> str | None:
    """A cache-busting version string for a board that shows the station logo.

    Folded into the board segment cache key so an operator's new logo (or a
    removal) re-renders the board instead of airing the previously cached
    picture. ``None`` when the board does not use the logo.
    """

    if not board_uses_station_logo(board):
        return None
    updated = station_logo_updated_at(work_dir, channel_id)
    return "station-logo-absent" if updated is None else f"station-logo-{updated:.6f}"


def with_station_logo(
    image_resolver: ImageResolver | None,
    *,
    work_dir: Path | str,
    channel_id: str,
) -> ImageResolver:
    """Wrap a board image resolver so ``station-logo`` means this channel's logo.

    Everything else is passed through to ``image_resolver`` unchanged; with no
    underlying resolver (no database) the logo still resolves, because it is a
    plain file the service owns. A missing logo resolves to ``None`` -- the
    zone renders empty, never crashes.
    """

    def resolve(ref: str) -> Path | None:
        if ref == STATION_LOGO_REF:
            return resolve_station_logo(work_dir, channel_id)
        if image_resolver is None:
            return None
        return image_resolver(ref)

    return resolve
