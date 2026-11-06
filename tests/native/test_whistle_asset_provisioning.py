# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Focused tests for the pinned Whistle asset producer."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

import civiccast.native.whistle_assets as assets


def test_downloader_stops_after_one_byte_over_the_pinned_size(tmp_path: Path, monkeypatch) -> None:
    class TrackedResponse(io.BytesIO):
        bytes_returned = 0

        def read(self, size: int = -1) -> bytes:
            chunk = super().read(size)
            self.bytes_returned += len(chunk)
            return chunk

    response = TrackedResponse(b"much larger than the reviewed response")

    class FakeOpener:
        def open(self, _request, *, timeout):
            return response

    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *_handlers: FakeOpener())
    destination = tmp_path / "asset.bin"

    try:
        assets.download_verified_file(
            "https://example.invalid/asset.bin",
            destination,
            expected_bytes=2,
            expected_sha256=hashlib.sha256(b"ok").hexdigest(),
        )
    except assets.WhistleAssetError as exc:
        assert "exceeds its reviewed size" in str(exc)
    else:
        raise AssertionError("an over-sized response must stop at the size bound")

    assert response.bytes_returned == 3
    assert not destination.exists()
    assert not list(tmp_path.glob(".*.partial"))


def test_provisioner_downloads_only_the_pinned_model_and_wheel_member(
    tmp_path: Path, monkeypatch
) -> None:
    model = b"reviewed-model"
    dll = b"reviewed-dll"
    wheel_buffer = io.BytesIO()
    with zipfile.ZipFile(wheel_buffer, "w") as archive:
        archive.writestr("needle/libneedle3.dll", dll)
        archive.writestr("needle/unrelated.txt", b"not staged")
    wheel = wheel_buffer.getvalue()
    model_sha = hashlib.sha256(model).hexdigest()
    dll_sha = hashlib.sha256(dll).hexdigest()
    wheel_sha = hashlib.sha256(wheel).hexdigest()

    monkeypatch.setattr(assets, "WHISTLE_MODEL_BYTES", len(model))
    monkeypatch.setattr(assets, "WHISTLE_MODEL_SHA256", model_sha)
    monkeypatch.setattr(assets, "WHISTLE_ENGINE_DLL_BYTES", len(dll))
    monkeypatch.setattr(assets, "WHISTLE_ENGINE_DLL_SHA256", dll_sha)
    monkeypatch.setattr(assets, "WHISTLE_ENGINE_WHEEL_BYTES", len(wheel))
    monkeypatch.setattr(assets, "WHISTLE_ENGINE_WHEEL_SHA256", wheel_sha)
    monkeypatch.setattr(
        assets,
        "WHISTLE_PACK_FILES",
        {"whistle.cact": (len(model), model_sha), "libneedle.dll": (len(dll), dll_sha)},
    )

    # Only CivicCast's own copies are served: provisioning must succeed without
    # any upstream request, and the moving `main` source URLs (kept as recorded
    # provenance) must never be fetched.
    responses = {
        assets.WHISTLE_MODEL_DOWNLOAD_URLS[0]: model,
        assets.WHISTLE_ENGINE_WHEEL_DOWNLOAD_URLS[0]: wheel,
    }

    class FakeOpener:
        def open(self, request, *, timeout):
            return io.BytesIO(responses[request.full_url])

    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *_handlers: FakeOpener())
    output = tmp_path / "pack-root"
    result = assets.provision_whistle_assets(output, cache=tmp_path / "cache")

    assert set(result) == {"whistle.cact", "libneedle.dll"}
    assert {path.name for path in output.iterdir()} == {"whistle.cact", "libneedle.dll"}
    assert (output / "whistle.cact").read_bytes() == model
    assert (output / "libneedle.dll").read_bytes() == dll


def test_whistle_downloads_use_fixed_revisions_of_the_recorded_files() -> None:
    """Upstream removed `python/` from needle3's `main` on 2026-11-05 (HTTP 404).

    A `resolve/main/` URL can change or vanish under a pinned SHA-256, so the
    builder fetches from an immutable 40-hex revision of the same repo path.
    """
    import re

    from civiccast.native import app_payload

    pairs = [
        (app_payload.WHISTLE_MODEL_SOURCE_URL, app_payload.WHISTLE_MODEL_DOWNLOAD_URL),
        (app_payload.WHISTLE_ENGINE_WHEEL_URL, app_payload.WHISTLE_ENGINE_WHEEL_DOWNLOAD_URL),
    ]
    for source, download in pairs:
        assert "/resolve/main/" not in download
        match = re.fullmatch(
            r"(https://huggingface\.co/[^/]+/[^/]+)/resolve/[0-9a-f]{40}/(.+)", download
        )
        assert match, download
        assert source == f"{match.group(1)}/resolve/main/{match.group(2)}"


def test_whistle_downloads_try_civiccast_own_copy_first() -> None:
    """CivicCast keeps byte-exact copies so upstream moves can't break a build."""
    from civiccast.native import app_payload

    release = (
        "https://github.com/scottconverse/civiccast-third-party/releases/download/whistle-3.1.0/"
    )
    for urls, upstream in (
        (app_payload.WHISTLE_MODEL_DOWNLOAD_URLS, app_payload.WHISTLE_MODEL_DOWNLOAD_URL),
        (
            app_payload.WHISTLE_ENGINE_WHEEL_DOWNLOAD_URLS,
            app_payload.WHISTLE_ENGINE_WHEEL_DOWNLOAD_URL,
        ),
    ):
        assert urls == (release + upstream.rsplit("/", 1)[1], upstream)


def _serve(monkeypatch, responses: dict[str, bytes]) -> list[str]:
    requested: list[str] = []

    class FakeOpener:
        def open(self, request, *, timeout):
            requested.append(request.full_url)
            if request.full_url not in responses:
                raise OSError("HTTP Error 404: Not Found")
            return io.BytesIO(responses[request.full_url])

    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *_handlers: FakeOpener())
    return requested


def test_download_falls_back_when_the_own_copy_is_missing(tmp_path: Path, monkeypatch) -> None:
    body = b"pinned bytes"
    requested = _serve(monkeypatch, {"https://upstream.example/f": body})
    result = assets.download_first_verified(
        ("https://mirror.example/f", "https://upstream.example/f"),
        tmp_path / "f",
        expected_bytes=len(body),
        expected_sha256=hashlib.sha256(body).hexdigest(),
    )
    assert result.read_bytes() == body
    assert requested == ["https://mirror.example/f", "https://upstream.example/f"]


def test_download_rejects_wrong_bytes_from_the_own_copy_and_falls_back(
    tmp_path: Path, monkeypatch
) -> None:
    body = b"pinned bytes"
    _serve(
        monkeypatch,
        {"https://mirror.example/f": b"tampered!!!!", "https://upstream.example/f": body},
    )
    result = assets.download_first_verified(
        ("https://mirror.example/f", "https://upstream.example/f"),
        tmp_path / "f",
        expected_bytes=len(body),
        expected_sha256=hashlib.sha256(body).hexdigest(),
    )
    assert result.read_bytes() == body
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_download_uses_the_own_copy_without_touching_upstream(tmp_path: Path, monkeypatch) -> None:
    body = b"pinned bytes"
    requested = _serve(monkeypatch, {"https://mirror.example/f": body})
    assets.download_first_verified(
        ("https://mirror.example/f", "https://upstream.example/f"),
        tmp_path / "f",
        expected_bytes=len(body),
        expected_sha256=hashlib.sha256(body).hexdigest(),
    )
    assert requested == ["https://mirror.example/f"]


def test_download_names_every_source_when_all_fail(tmp_path: Path, monkeypatch) -> None:
    _serve(monkeypatch, {})
    with pytest.raises(assets.WhistleAssetError) as excinfo:
        assets.download_first_verified(
            ("https://mirror.example/f", "https://upstream.example/f"),
            tmp_path / "f",
            expected_bytes=3,
            expected_sha256="0" * 64,
        )
    message = str(excinfo.value)
    assert "any source" in message
    assert "mirror.example" in message and "upstream.example" in message
    assert not (tmp_path / "f").exists()


def test_whistle_pack_contract_keeps_the_recorded_source_urls() -> None:
    """The installer validates this metadata exactly; changing it breaks pack/installer pairs."""
    from civiccast.native import app_payload

    contract = app_payload.WHISTLE_PACK_CONTRACT
    assert contract["model_source_url"] == (
        "https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact"
    )
    assert contract["engine_wheel_source_url"] == (
        "https://huggingface.co/Cactus-Compute/needle3/resolve/main/"
        "python/cactus_needle-3.1.0-py3-none-win_amd64.whl"
    )


def test_downloader_rejects_non_https_urls_before_opening(tmp_path: Path, monkeypatch) -> None:
    class FakeOpener:
        def open(self, *_args, **_kwargs):
            raise AssertionError("non-HTTPS URLs must be rejected before opening")

    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *_handlers: FakeOpener())
    with pytest.raises(assets.WhistleAssetError, match="HTTPS"):
        assets.download_verified_file(
            "file:///etc/passwd",
            tmp_path / "asset.bin",
            expected_bytes=1,
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
        )


def test_downloader_rejects_https_to_http_redirects() -> None:
    request = assets.urllib.request.Request("https://huggingface.co/asset")
    with pytest.raises(assets.WhistleAssetError, match="HTTPS"):
        assets._HttpsOnlyRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "http://huggingface.co/asset"
        )


def test_provisioner_refuses_an_unexpected_asset_root_file(tmp_path: Path) -> None:
    output = tmp_path / "pack-root"
    output.mkdir()
    (output / "extra.dll").write_bytes(b"unreviewed")

    try:
        assets.provision_whistle_assets(output, cache=tmp_path / "cache")
    except assets.WhistleAssetError as exc:
        assert "unexpected files" in str(exc)
    else:
        raise AssertionError("an extra pack file must fail closed")
